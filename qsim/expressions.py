"""Small vector expression language. No Python eval, attributes, or I/O."""
import ast
import operator
import re

import numpy as np
import pandas as pd

from .operations import standardize

FUNCTIONS = {'abs': np.abs, 'sqrt': np.sqrt, 'log': np.log, 'exp': np.exp,
             'minimum': np.minimum, 'maximum': np.maximum, 'clip': np.clip,
             'rank': lambda x: pd.Series(x).rank(pct=True).to_numpy(), 'zscore': standardize}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow}


class Expression:
    def __init__(self, text):
        self.text = text
        normalized = re.sub(r'<([A-Za-z_]\w*)>', r'\1', text)
        try:
            self.tree = ast.parse(normalized, mode='eval').body
        except SyntaxError as error:
            raise ValueError(f'Invalid alpha expression: {text}') from error
        self.names = set()
        if len(list(ast.walk(self.tree))) > 256:
            raise ValueError('Expression exceeds 256 AST nodes')
        self._validate(self.tree)

    def _validate(self, node):
        if isinstance(node, ast.Constant) and type(node.value) in (int,float) and np.isfinite(node.value):
            return
        if isinstance(node, ast.Name):
            self.names.add(node.id); return
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            self._validate(node.left); self._validate(node.right); return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            self._validate(node.operand); return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            for arg in node.args:
                self._validate(arg)
            return
        raise ValueError(f'Unsupported expression syntax: {ast.dump(node)}')

    def evaluate(self, values, size):
        def visit(node):
            # NumPy scalars bound numeric work; never construct gigantic Python integers.
            if isinstance(node,ast.Constant): return np.float64(node.value)
            if isinstance(node,ast.Name): return values[node.id]
            if isinstance(node,ast.BinOp): return OPS[type(node.op)](visit(node.left),visit(node.right))
            if isinstance(node,ast.UnaryOp):
                value=visit(node.operand)
                return -value if isinstance(node.op,ast.USub) else value
            return FUNCTIONS[node.func.id](*[visit(arg) for arg in node.args])
        with np.errstate(all='ignore'):
            value=np.broadcast_to(np.asarray(visit(self.tree),dtype=float),(size,)).copy()
        value[~np.isfinite(value)]=np.nan
        return value


def dependency_levels(specs):
    names={f['name'] for f in specs}
    expressions={f['name']:Expression(f['expression']) for f in specs if f['kind']=='expression'}
    pending={f['name']:set(expressions[f['name']].names) if f['name'] in expressions else set() for f in specs}
    for name,deps in pending.items():
        if not deps <= names:
            raise ValueError(f'{name}: unknown expression inputs {deps-names}')
    levels=[];done=set()
    while pending:
        level=[f['name'] for f in specs if f['name'] in pending and pending[f['name']]<=done]
        if not level: raise ValueError('Cyclic factor expression dependencies')
        levels.append(level);done.update(level)
        for name in level: del pending[name]
    return levels,expressions
