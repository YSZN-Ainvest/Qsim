import numpy as np
import pandas as pd


def standardize(x):
    out = np.full_like(x, np.nan, dtype=float)
    good = np.isfinite(x)
    if good.sum() < 2:
        return out
    sigma = np.std(x[good])
    if sigma > 1e-12:
        out[good] = (x[good] - np.mean(x[good])) / sigma
    return out


def neutralize(y, styles=None, industries=None):
    """Cross-sectional OLS with intercept, continuous styles and categorical dummies."""
    good = np.isfinite(y)
    columns = [np.ones(len(y))]
    if styles is not None:
        for col in styles:
            x = pd.to_numeric(styles[col], errors='coerce').to_numpy(float)
            good &= np.isfinite(x)
            columns.append(x)
    if industries is not None:
        for col in industries:
            s = industries[col]
            good &= s.notna().to_numpy()
            # A reference category is dropped; lstsq also handles redundant styles.
            columns.extend(pd.get_dummies(s,drop_first=True,dtype=float).to_numpy().T)
    design = np.column_stack(columns)
    out = np.full_like(y,np.nan,dtype=float)
    if good.sum() <= design.shape[1] + 1:
        return out
    design = design[good]
    if design.shape[1] > 1:
        scale = np.std(design[:,1:],axis=0)
        design[:,1:] = (design[:,1:] - np.mean(design[:,1:],axis=0)) / np.where(scale>1e-12,scale,1)
    out[good] = y[good] - design @ np.linalg.lstsq(design,y[good],rcond=None)[0]
    return out


def process(x, eligible, options, exposures=None):
    x = np.where(eligible,x,np.nan).astype(float)
    good = np.isfinite(x)
    if good.sum() < options['min_cross_section']:
        return np.full_like(x,np.nan)
    if options['winsor_mad'] > 0:
        med = np.median(x[good]); mad = np.median(np.abs(x[good]-med))*1.4826
        if mad > 1e-12:
            x[good] = np.clip(x[good],med-options['winsor_mad']*mad,med+options['winsor_mad']*mad)
    styles, industries = options['neutralize_styles'], options['neutralize_industries']
    if styles or industries:
        if exposures is None:
            raise ValueError('Neutralization requires historical point-in-time exposures')
        x = neutralize(x,exposures[styles],exposures[industries])
    return standardize(x) if options['standardize'] else x


def groups_from_score(score, count):
    good = np.flatnonzero(np.isfinite(score))
    result = np.full(len(score),-1,dtype=int)
    if len(good) < count or np.std(score[good]) < 1e-12:
        return result
    # Stable ties: the input security axis is sorted by full identifier.
    order = good[np.argsort(score[good],kind='stable')]
    result[order] = np.minimum(np.arange(len(order))*count//len(order),count-1)
    return result
