# Доказательства локальной проверки .58

`native-authority-stock.txt`: 56 методов (33 Stock-контракта A/B/C + 23 метода C/authority). `standard-mrp.txt`: 4 проверки со стандартными non-C scrap/производством/unbuild и блокировкой прежнего WO-only обхода. `pure-plans.txt`: 33 чистых теста B/C. Все финальные runner results — 0 failed / 0 errors; содержимое логов сохранено, убраны только хвостовые пробелы.

Это **не** полный Enterprise/ERP-UA прогон. Состав адаптера, объявленные вместо отсутствующих EE модели и точные SHA-256 рабочих копий описаны в `provenance.json` и `declarations.py`. Реальные условия валидации матрицы и EE write lifecycle не заменены успешным результатом контрактов. `native_fixture.txt` создаёт реальные MRP 5 в карантин, а QC-вход задаёт на объявленной границе.

Тела проверок находятся в `extra-addons/quality_vataga/tests/test_quality_check_measurement_matrix.py`. В native адаптер извлечены без изменения 21 новый метод и два существующих (дедупликация нескольких QC / pending FAIL). Подготовка/полные локальные адаптеры сохранены в `output/quality-stage-c-authority-validation` и `output/quality-stage-c-review`, без включения БД, filestore и зависимостей в Git. `test_guard_regression.py` показывает изменённое ожидание прежнего P1-воспроизводителя: ошибка до движения, Q=2 / Stock=3.

Команда общего прогона:

```text
odoo-bin --addons-path=<community>/addons,<checkout>/output/quality-incoming-stage-a-validation/addons,<checkout>/output/quality-stage-c-authority-validation/addons
  --data-dir=<checkout>/output/quality-stage-c-authority-validation/odoo-data
  --db_host=127.0.0.1 --db_port=55529 --db_user=odoo --database=quality_c_authority_native
  --update=quality_incoming_stock_contract,quality_c_authority_contract
  --without-demo=all --test-enable --test-tags=/quality_incoming_stock_contract,/quality_c_authority_contract
  --stop-after-init --no-http
```

Чистые тесты запускаются напрямую Python: `test_production_release_plan.py`, `test_production_residual_plan.py`. Полный EE runner допустим только в отдельной одноразовой лицензированной базе; на общей staging-базе он не запускался.
