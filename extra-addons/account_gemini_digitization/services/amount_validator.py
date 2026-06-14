class AmountValidator:
    def __init__(self, env):
        self.env = env

    def validate_job(self, job):
        return {
            'is_valid': True,
            'warnings': [],
            'errors': [],
        }
