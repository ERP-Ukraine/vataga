class ProductMatcher:
    def __init__(self, env):
        self.env = env

    def match_line(self, line_values, partner=None):
        return {
            'product': self.env['product.product'],
            'status': 'not_found',
            'score': 0.0,
            'method': False,
            'candidates': [],
        }
