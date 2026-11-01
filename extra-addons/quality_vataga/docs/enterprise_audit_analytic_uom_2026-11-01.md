# Этап 13.3: диагностика Enterprise-аудита и единицы аналитики

Основа: `origin/staging`, `780dd65`. Версия quality_vataga **17.0.2.62**.

## Воспроизведённая причина

На Community с реальным `account_analytic_uom` воспроизведено именно
преобразование Hours → Units. До исправления настоящий ORM-тест завершился
UserError. Traceback сохранён в `evidence/enterprise_audit_uom_before_fix.txt`:

```text
test_real_native_analytics_are_cumulative_while_stock_uses_incremental_cost
  → env.flush_all()
  → stored field recompute
  → account_analytic_uom.AccountAnalyticLine._compute_unit_quantity_amount()
  → uom.uom._compute_quantity(hours, product.uom_id)
  → UserError: Hours ... Units
```

Штатный `mrp_account` намеренно создаёт строку с готовым товаром в `product_id`
и часами в `product_uom_id`. Количество этой аналитики — затраченное время.
Community сохраняет такую строку; несовместимое преобразование добавлял
отчётный `unit_quantity_amount` из локального `account_analytic_uom`.

Это доказывает конкретную цепочку на локальном настоящем ORM. Наличие именно
этого переопределения и точное место deferred recompute в реальном Enterprise
подтвердит следующий staging-отчёт; прямого доступа к Enterprise ORM нет.
Первый воспроизводящий запуск также обнаруживал auto-install `mrp_vataga`
из общего пути addons. Исправленный runner копирует только настоящий
`account_analytic_uom` в изолированный addons-path и не подключает прочие
локальные модули. Финальная регрессия проверяет минимальную комбинацию.

## Исправление отчётного количества

Для `category=manufacturing_order`, единицы категории Working Time и товара
с другой категорией единиц показатель количества изделий неприменим:
`unit_quantity_applicable=False`. Его вклад в агрегат количества изделий —
`unit_quantity_amount=0`. **Сумма затрат `amount` и часы `unit_amount`
сохраняются**, как и товар, его единицы и штатная аналитическая строка.
Нулевое значение относится только к отчётному количеству изделий, не к
оборудованию, сотрудникам или стоимости производства.

Признак применимости вычисляемый, не stored: новых столбцов нет. Для товара,
сам измеряемого в часах, минутная аналитика по-прежнему штатно преобразуется
в количество товара. Для других количественных строк продолжает вызываться
обычный `_compute_quantity()` с включённым `raise_if_failure`. Другие
несовместимые категории остаются ошибкой. Конверсия часов в штуки напрямую
также по-прежнему вызывает UserError.

Единицы реальных и тестовых товаров не изменяются. Нет изменений ACL,
пользователей, native MRP/Stock-методов, ставок и формулы себестоимости.
Enterprise `_cal_cost()` остаётся заблокированным до интеграционной проверки.

## Диагностика следующего Scheduled Action

Без изменения кода Scheduled Action новый отчёт содержит:

- `diagnostics.stage` и журнал входа в этапы `diagnostics.stages`;
- отдельные этапы создания счетов/центра/сотрудников/товаров/BOM/MO,
  подтверждения MO, чтения WO, причин потерь, первого/второго интервала и flush;
- `error_stage`, `traceback`, `error_frames` при исключении, включая файл,
  строку и метод. Значения локальных переменных из frame не выгружаются;
- фактическую MRO/исходники create/write аналитических строк,
  `_perform_analytic_distribution`, compute количества и `_compute_quantity`;
- метаданные compute/dependencies аналитических полей и единицы временных
  товаров; в baseline — единицы каждой настоящей аналитической строки,
  категории единиц и оба отчётных поля количества, если модуль установлен.

Добавление меток не вызывает промежуточных flush и не меняет порядок
штатных пересчётов. Это сохраняет способность обнаружить запаздывающую
Enterprise-аналитику. Отдельный курсор всегда откатывается; только после
успешного rollback выставляется `rollback=COMPLETE`. Ошибка самой проверки
отката также возвращается с traceback; при неподтверждённом откате значение
будет `NOT_CONFIRMED`. Все 14 сценариев и подсчёт PASS/FAIL/NOT_RUN сохранены.
Отсутствие исходника или невыполненный сценарий не создают фиктивный traceback
и не становятся успешной проверкой.

## Проверки

- **63** Community ORM-теста без опциональных модулей.
- **69** ORM-тестов с реальными `account_analytic_uom` и
  `mrp_subcontracting_account`: прежние 63 плюс 6 новых UoM-регрессий.
- **198** автономных контрактов; Python/XML, import определений обоих
  модулей, safe_eval-компиляция Scheduled Actions.
- Подтверждены штатные analytics −60 → −75, часы 1 → 1.25,
  Stock `140 + 35 = 175`, неизменность analytic IDs и единиц товара.
- Проверены dozen → units, minutes → hours для временного time-product,
  отказ hour → piece вне MRP-времени, отказ kg → piece даже в MRP,
  пересчёт при смене категории, rollback, этап и настоящий native traceback.
- Сохранены `190 = 152 + 38`, ограничения провайдеров, immutable checkpoint,
  вложенный rollback, ACL временных категорий и запрет ложного PASS.

Журналы: `evidence/enterprise_audit_uom_community_orm.txt`,
`evidence/enterprise_audit_uom_subcontracting_orm.txt`,
`evidence/enterprise_audit_uom_portable.txt`.

После доставки Python-кода **обоих** модулей в staging и перезапуска службы
повторить тот же `enterprise_employee_cost_audit_scheduled_action.py`.
Публикация выполняется только в `origin/staging`; main и production
не затрагиваются. Успех локальных тестов не является разрешением Enterprise
провайдера или приёмкой производственного потока 4 PASS + 1 FAIL.
