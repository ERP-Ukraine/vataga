"""Run portable contracts only; Enterprise/Community ORM suites run separately."""
import ast
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    suite = unittest.TestSuite()
    for path in sorted(root.glob('test_*contract.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        needs_odoo = any(
            isinstance(node, ast.ImportFrom) and (node.module or '').startswith('odoo')
            for node in tree.body
        )
        if not needs_odoo:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(path.stem))
    if not suite.countTestCases():
        raise RuntimeError('No portable contract tests were found')
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    sys.exit(main())
