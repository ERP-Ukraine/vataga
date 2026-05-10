import logging
from datetime import datetime

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    NBU_EXCHANGE_URL = 'https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange'
    NBU_REQUEST_TIMEOUT = 15

    @api.model
    def _cron_update_nbu_currency_rates(self):
        return self._update_nbu_currency_rates(raise_on_error=False)

    def action_update_nbu_currency_rates(self):
        self.ensure_one()
        result = self.env['res.currency']._update_nbu_currency_rates(
            raise_on_error=True,
            currencies=self,
        )
        message = _('Оновлено: %(updated)s. Пропущено: %(skipped)s. Помилок: %(errors)s.') % {
            'updated': result['updated_count'],
            'skipped': result['skipped_count'],
            'errors': result['error_count'],
        }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Оновлення курсів НБУ'),
                'message': message,
                'type': 'warning' if result['error_count'] or result['skipped_count'] else 'success',
                'sticky': False,
            },
        }

    @api.model
    def _update_nbu_currency_rates(self, raise_on_error=False, currencies=None):
        company = self.env.company
        company_currency = company.currency_id
        if company_currency.name != 'UAH':
            message = _(
                'NBU currency rates are UAH-based and can only be updated when the company currency is UAH. '
                'Company %(company)s uses %(currency)s, so no currency rates were changed.',
            ) % {
                'company': company.display_name,
                'currency': company_currency.name,
            }
            _logger.warning(message)
            if raise_on_error:
                raise UserError(message)
            return {
                'updated': [],
                'skipped': [],
                'errors': [message],
                'updated_count': 0,
                'skipped_count': 0,
                'error_count': 1,
            }

        if currencies is None:
            currencies = self.search([('active', '=', True)])
            currencies = currencies - company_currency
        else:
            currencies = currencies.filtered(lambda currency: currency.active)
            currencies = currencies - company_currency

        _logger.info(
            'Starting NBU currency rate update for company %s (%s). Found %s active currencies.',
            company.display_name,
            company_currency.name,
            len(currencies),
        )

        updated = []
        skipped = []
        errors = []

        for currency in currencies:
            currency_code = (currency.name or '').upper()
            try:
                nbu_rate, rate_date = self._fetch_nbu_rate(currency_code)
                if not nbu_rate:
                    _logger.warning('NBU did not return a rate for currency %s.', currency_code)
                    skipped.append(currency_code)
                    continue

                self._create_or_update_nbu_rate(currency, nbu_rate, rate_date, company)
                updated.append(currency_code)
                _logger.info(
                    'Updated NBU rate for %s on %s: 1 %s = %s UAH.',
                    currency_code,
                    rate_date,
                    currency_code,
                    nbu_rate,
                )
            except Exception as error:
                _logger.exception('Failed to update NBU rate for currency %s.', currency_code)
                errors.append('%s: %s' % (currency_code, error))
                continue

        result = {
            'updated': updated,
            'skipped': skipped,
            'errors': errors,
            'updated_count': len(updated),
            'skipped_count': len(skipped),
            'error_count': len(errors),
        }
        _logger.info(
            'Finished NBU currency rate update. Updated: %s. Skipped: %s. Errors: %s.',
            ', '.join(updated) or '-',
            ', '.join(skipped) or '-',
            '; '.join(errors) or '-',
        )

        if raise_on_error and errors and not updated:
            raise UserError(
                _(
                    'NBU currency rate update failed. Please try again later.\n\n%s',
                    '\n'.join(errors),
                )
            )

        return result

    @api.model
    def _fetch_nbu_rate(self, currency_code, date=None):
        params = {
            'valcode': currency_code,
            'json': '',
        }
        if date:
            params['date'] = fields.Date.to_date(date).strftime('%Y%m%d')

        try:
            response = requests.get(
                self.NBU_EXCHANGE_URL,
                params=params,
                timeout=self.NBU_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            _logger.error('NBU API request failed for %s: %s', currency_code, error)
            raise

        try:
            data = response.json()
        except ValueError as error:
            _logger.error('Unable to parse NBU API response for %s: %s', currency_code, error)
            raise

        if not data:
            return False, fields.Date.context_today(self)

        rate_data = data[0]
        try:
            nbu_rate = float(rate_data['rate'])
            if nbu_rate <= 0:
                raise ValueError(_('NBU rate must be positive.'))
            rate_date = self._parse_nbu_exchange_date(rate_data.get('exchangedate'))
        except (KeyError, TypeError, ValueError) as error:
            _logger.error('Invalid NBU API payload for %s: %s', currency_code, rate_data)
            raise ValueError(_('Invalid NBU API payload for %s: %s') % (currency_code, error)) from error

        return nbu_rate, rate_date

    @api.model
    def _parse_nbu_exchange_date(self, exchange_date):
        if not exchange_date:
            return fields.Date.context_today(self)

        for date_format in ('%d.%m.%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(exchange_date, date_format).date()
            except ValueError:
                continue

        _logger.warning(
            'Unable to parse NBU exchangedate %s. Falling back to current date.',
            exchange_date,
        )
        return fields.Date.context_today(self)

    @api.model
    def _create_or_update_nbu_rate(self, currency, nbu_rate, rate_date, company):
        if company.currency_id.name != 'UAH':
            _logger.warning(
                'Skipping NBU rate write for %s because company %s currency is %s, not UAH.',
                currency.name,
                company.display_name,
                company.currency_id.name,
            )
            return False

        rate_model = self.env['res.currency.rate'].sudo()
        rate_values = {
            'currency_id': currency.id,
            'company_id': company.root_id.id,
            'name': rate_date,
        }

        if 'inverse_company_rate' in rate_model._fields:
            # Odoo 17 exposes inverse_company_rate as "company currency per 1 unit".
            # NBU returns the same direct UAH quote, so writing it here keeps the
            # technical rate inverse and converts 100 USD to about 4100 UAH at 41.00.
            rate_values['inverse_company_rate'] = nbu_rate
        else:
            # Older Odoo versions only store the technical inverse rate.
            rate_values['rate'] = 1.0 / nbu_rate

        existing_rate = rate_model.search(
            [
                ('currency_id', '=', currency.id),
                ('company_id', '=', company.root_id.id),
                ('name', '=', rate_date),
            ],
            limit=1,
        )
        if existing_rate:
            existing_rate.write(rate_values)
        else:
            rate_model.create(rate_values)

        return True
