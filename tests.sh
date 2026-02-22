#!/usr/bin/env bash
set -o errexit

git submodule init
git submodule update --remote
REPO="erpukraine/odoo-ee-erpu" PROJECT="vataga" VERSION="v1.0"
IMAGE_NAME=${REPO}:${PROJECT}-${VERSION}
UPDATED_MODULES=`git diff --diff-filter=d --name-only HEAD~1..HEAD \
    | egrep -o "^[^\/]+\/" | sed 's/.$//' | grep -v '^\.' | uniq \
    | awk -vORS=, '{ print $1 }' | sed 's/,$/\n/'`
if [ -z ${UPDATED_MODULES} ]; then echo "Nothing to test"; exit 0; fi
echo "Testing ${UPDATED_MODULES}"
sed -i -e 's/without_demo = True/without_demo = False/g' odoo.conf
sed -i -e "s/db_host =.*/db_host = localhost/g" odoo.conf
docker build -t ${IMAGE_NAME} .
mkdir ./data && chmod 777 ./data
docker run --rm -t --name=${PROJECT}-${VERSION} \
    --network=host \
    -v ./data:/var/lib/odoo \
    -e "HOST=localhost" ${IMAGE_NAME} \
    -i base,web,${UPDATED_MODULES} -d test-db \
    --db_host=localhost --db-filter=test-db \
    -w odoo -r odoo --workers=0 --stop-after-init
docker run --rm -t --name=${PROJECT}-${VERSION} \
    --network=host \
    -v ./data:/var/lib/odoo -e "HOST=localhost" \
    -e "COVERAGE_FILE=/tmp/.coverage" ${IMAGE_NAME} \
    /bin/bash -c "set -e; coverage run /usr/bin/odoo \
    -u ${UPDATED_MODULES} --workers=0 -d test-db --stop-after-init --test-enable -w odoo -r odoo \
    --db_host localhost --db-filter=test-db; coverage report \
    --omit */system_site_packages/*,*/site-packages/*,*/dist-packages/*,*/pyshared/*,*/enterprise-addons/* "
