FROM erpukraine/odoo-ee-erpu:17.0-latest
COPY --chown=odoo:odoo extra-addons/ /mnt/extra-addons
COPY --chown=odoo:odoo odoo.conf /etc/odoo/
