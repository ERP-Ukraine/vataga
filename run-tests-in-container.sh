#!/bin/bash

RED='\033[0;31m'
GRN='\033[0;32m'
PRP='\033[0;35m'
NC='\033[0m'

DBNAME="odoo-tests"
DBHOST="pg13odoo"

if [[ $1 == "rm" ]]; then
    if psql -lqt | cut -d \| -f 1 | grep -qw "${DBNAME}"; then
        echo -e "${RED}Deleting '${DBNAME}' Database${NC}"
        dropdb "${DBNAME}"
        echo -e "${RED}Deleting filestore '~/.local/share/Odoo/filestore/${DBNAME}'${NC}"
        rm -rf "~/.local/share/Odoo/filestore/${DBNAME}"
    else
        echo -e "${GRN}Database '"${DBNAME}"' does not exists.${NC}"
    fi
    exit 0
fi

# Find what to test
# Either modules list from arg or modules updated in last commit
UPDATED_MODULES=`git diff --diff-filter=d --name-only HEAD~1..HEAD \
    | egrep -o "^[^\/]+\/" | sed 's/.$//' | uniq \
    | awk -vORS=, '{ print $1 }' | sed 's/,$/\n/'`
if [ -z ${UPDATED_MODULES} ]; then UPDATED_MODULES="all"; fi

if [[ $1 != "" ]]; then
    UPDATED_MODULES=$1
fi

psql -lqt | cut -d \| -f 1 | grep -qw "${DBNAME}"
if [ $? -ne 0 ]; then
    psql -q -c "ALTER SYSTEM SET max_connections = '64';"
    psql -q -c "ALTER SYSTEM SET shared_buffers = '1GB';"
    psql -q -c "ALTER SYSTEM SET effective_cache_size = '3GB';"
    psql -q -c "ALTER SYSTEM SET maintenance_work_mem = '256MB';"
    psql -q -c "ALTER SYSTEM SET default_statistics_target = '100';"
    psql -q -c "ALTER SYSTEM SET random_page_cost = '1.0';"
    psql -q -c "ALTER SYSTEM SET effective_io_concurrency = '200';"
    psql -q -c "ALTER SYSTEM SET work_mem = '64MB';"
    psql -q -c "ALTER SYSTEM SET max_worker_processes = '8';"
    psql -q -c "ALTER SYSTEM SET max_parallel_workers_per_gather = '4';"
    psql -q -c "ALTER SYSTEM SET max_parallel_workers = '8';"
    echo -e "${GRN}Installing modules${NC}"
    odoo --stop-after-init --no-http --config=./odoo-devc.conf \
        --workers=0 --database="${DBNAME}" --init=${UPDATED_MODULES}
fi

echo -e "${PRP}\n\nTesting modules: ${UPDATED_MODULES}\n\n${NC}"
coverage run \
    /usr/bin/odoo -u ${UPDATED_MODULES} --workers=0 -d ${DBNAME} --config=./odoo-devc.conf \
        --stop-after-init --test-enable --test-tags standard,external,post_install_l10n --db-filter=${DBNAME} \
&& coverage report \
    --omit "*/system_site_packages/*,*/site-packages/*,*/dist-packages/*,*/pyshared/*,*/enterprise-addons/*"
