import re

from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    _ACTIVITY_FORMS = (
        "ОСББ",
        "ПРАТ",
        "ФОП",
        "ТОВ",
        "ТДВ",
        "ПП",
        "ДП",
        "КП",
        "ГО",
        "БФ",
        "АТ",
    )
    _CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
    _ACTIVITY_FORM_RE = re.compile(
        r"(?:^| )(?:%s)$"
        % "|".join(map(re.escape, sorted(_ACTIVITY_FORMS, key=len, reverse=True)))
    )

    def _normalize_partner_name(self, name):
        if not isinstance(name, str):
            return name
        return " ".join(name.strip().upper().split())

    def _get_counterparty_company_type_from_values(self, values, default_company_type="person"):
        company_type = values.get("company_type")
        if company_type is None and "is_company" in values:
            company_type = "company" if values["is_company"] else "person"
        return company_type if company_type is not None else default_company_type

    def _should_validate_counterparty_name_from_values(self, values):
        company_type = self._get_counterparty_company_type_from_values(values)
        parent_id = values.get("parent_id")
        return company_type == "company" and not parent_id

    def _get_partner_name_format_message(self):
        activity_forms = ", ".join(self._ACTIVITY_FORMS)
        return _(
            "Назва контрагента зберігається у верхньому регістрі з одним пробілом між словами та без спецсимволів. "
            "Дозволені лише літери, цифри, пробіл, '+' та '-'. "
            "Якщо назва містить кирилицю, вона має завершуватися однією з форм діяльності: %s. "
            "Приклади: 'ВАТАГА ТОВ', 'ІВАНЕНКО ІВАН ІВАНОВИЧ ФОП', 'VATAGA TRADE LLC'."
        ) % activity_forms

    def _is_allowed_partner_name_character(self, char):
        return char.isalpha() or char.isdigit() or char in {" ", "+", "-"}

    def _get_partner_name_validation_error(self, name):
        if not name:
            return False
        if name != self._normalize_partner_name(name):
            return self._get_partner_name_format_message()
        if any(not self._is_allowed_partner_name_character(char) for char in name):
            return self._get_partner_name_format_message()
        if self._CYRILLIC_RE.search(name) and not self._ACTIVITY_FORM_RE.search(name):
            return self._get_partner_name_format_message()
        return False

    def _should_validate_counterparty_name(self):
        self.ensure_one()
        # Business rule: validate only standalone counterparty cards, not child
        # contacts or addresses inside the company card.
        return self.company_type == "company" and not self.parent_id

    def _should_validate_counterparty_name_after_values(self, values):
        self.ensure_one()
        company_type = self._get_counterparty_company_type_from_values(
            values,
            default_company_type=self.company_type,
        )
        parent_id = values.get("parent_id", self.parent_id.id)
        return company_type == "company" and not parent_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "name" in vals and self._should_validate_counterparty_name_from_values(vals):
                vals["name"] = self._normalize_partner_name(vals["name"])
        return super().create(vals_list)

    def write(self, vals):
        if "name" not in vals:
            return super().write(vals)

        to_normalize = self.filtered(
            lambda partner: partner._should_validate_counterparty_name_after_values(vals)
        )
        untouched = self - to_normalize
        result = True

        if to_normalize:
            normalized_vals = dict(vals, name=self._normalize_partner_name(vals["name"]))
            result = super(ResPartner, to_normalize).write(normalized_vals)
        if untouched:
            result = super(ResPartner, untouched).write(vals) and result
        return result

    @api.constrains("name", "company_type", "parent_id")
    def _check_partner_name_format(self):
        for partner in self:
            if not partner.name or not partner._should_validate_counterparty_name():
                continue
            error_message = partner._get_partner_name_validation_error(partner.name)
            if error_message:
                raise ValidationError(error_message)

    @api.onchange("name", "parent_id", "company_type")
    def _onchange_partner_name_format(self):
        self.ensure_one()
        if not self.name:
            return

        if not self._should_validate_counterparty_name():
            return

        self.name = self._normalize_partner_name(self.name)

        error_message = self._get_partner_name_validation_error(self.name)
        if error_message:
            return {
                "warning": {
                    "title": _("Некоректний формат назви контрагента"),
                    "message": error_message,
                }
            }
