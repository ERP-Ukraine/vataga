class ResponseParser:
    def parse(self, response):
        return {
            'header': {},
            'lines': [],
            'raw': response,
        }
