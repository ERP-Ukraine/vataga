# Локальный аудит C guard

Это диагностический стенд Community Stock/MRP, а не замена Enterprise Quality.

`test_guard_review.py` содержит четыре новых проверки. `test_reproduce_workorder_only_c_scrap_consumes_released_stock` специально утверждает фактически небезопасный результат Stock=1 / Q=2: успешный runner подтверждает **дефект**, а не успешность защиты.

В `declarations.py` объявлены только поля признака/связи C-случая, необходимые для запуска неизменённого `production_case_stock_guards.py`. Жизненный цикл Enterprise Alert не моделируется и не проверяется. Временный адаптер и его исходники находятся в `output/quality-stage-c-review/addons/quality_c_guard_contract`; guard и residual helper скопированы из commit без изменений, SHA-256 в `provenance.json`.

В полном прогоне `stock-mrp.txt` выполнено 37 методов: прежние 33 Stock-контракта A/B/C плюс четыре новых диагностических. Новые non-C производство 5 → unbuild 2 дают остаток 3 стандартными методами MRP. Короткие SQL serialization failures в конкурентных A-тестах штатно повторяются Odoo; финальных ошибок runner нет.

Запуск в изолированной локальной БД, ни staging, ни production не использовались:

```text
odoo-bin --addons-path=<community>/addons,<checkout>/output/quality-incoming-stage-a-validation/addons,<checkout>/output/quality-stage-c-review/addons
  --data-dir=<checkout>/output/quality-stage-c-review/odoo-data
  --db_host=127.0.0.1 --db_port=55529 --db_user=odoo --database=quality_stage_c_guard_review
  --update=quality_incoming_stock_contract,quality_c_guard_contract
  --without-demo=all --test-enable --test-tags=/quality_incoming_stock_contract,/quality_c_guard_contract
  --stop-after-init --no-http
```
