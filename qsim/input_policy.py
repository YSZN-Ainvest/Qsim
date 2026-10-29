"""Availability-aware input interventions, before pivoting or factor preprocessing."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class InputPolicy:
    def __init__(self, mode='full', cutoff=None, seed=0):
        if mode not in ('full', 'truncate', 'perturb'):
            raise ValueError('Unknown input intervention')
        self.mode, self.cutoff, self.seed = mode, cutoff, seed
        self.files = {}
        self.changed_rows = 0

    def record(self, path):
        path = str(Path(path).resolve())
        digest = file_hash(path)
        if path in self.files and self.files[path] != digest:
            raise ValueError(f'Input changed during run: {path}')
        self.files[path] = digest

    def apply(self, frame, available_at, identity, columns=None):
        if self.mode == 'full':
            return frame
        future = np.asarray(available_at > self.cutoff)
        self.changed_rows += int(future.sum())
        if self.mode == 'truncate':
            return frame.loc[~future].copy()
        frame = frame.copy()
        token = hashlib.sha256(f'{self.seed}:{identity}'.encode()).digest()
        rng = np.random.default_rng(int.from_bytes(token[:8], 'little'))
        protected = {'order_book_id', 'available_at', 'bar_id', 'date', 'datetime'}
        for col in columns or [c for c in frame if c not in protected]:
            if pd.api.types.is_bool_dtype(frame[col]):
                frame.loc[future, col] = ~frame.loc[future, col]
            elif pd.api.types.is_numeric_dtype(frame[col]):
                frame[col] = frame[col].astype(float)
                values = frame.loc[future, col].to_numpy(float)
                frame.loc[future, col] = values * rng.uniform(.2, 3., len(values)) + rng.uniform(.01, .1, len(values))
            elif col in ('is_st', 'in_universe', 'st_status', 'suspended'):
                mapping = {True: False, False: True, 'true': 'false', 'false': 'true',
                           'True': 'False', 'False': 'True', '1': '0', '0': '1'}
                frame.loc[future, col] = frame.loc[future, col].map(mapping)
            else:
                frame.loc[future, col] = '__livetest_future__'
        return frame
