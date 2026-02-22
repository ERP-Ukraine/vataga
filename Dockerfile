ARG SAAS_IMG
FROM ${SAAS_IMG}
COPY --chown=odoo:odoo extra-addons/ /mnt/extra-addons
COPY --chown=odoo:odoo odoo.conf /etc/odoo/
