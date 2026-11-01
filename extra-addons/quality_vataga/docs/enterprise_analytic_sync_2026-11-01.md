# Этап 13.3: порядок duration/аналитики и точные сравнения

Основа `origin/staging`, `9153a8e`; версия **17.0.2.62**.
Полученный Enterprise-результат до исправления: 12 PASS, 2 FAIL, 0 NOT_RUN,
rollback COMPLETE. Сам по себе текст двух FAIL не указывал изменившиеся поля.

## Причина и границы доказательства

Полученные исходники staging показывают:

1. `mrp_workorder_hr_account._compute_duration()` вызывает аналитический helper
   **до** super.
2. `mrp_workorder._compute_duration()` затем присваивает фактическую duration.
3. В показанной ветке Enterprise вызывает нижележащие реализации на пустом
   recordset. Штатный post-super helper `mrp_account` не получает эту WO.

На настоящем Community ORM воспроизведён именно такой порядок вызовов против
штатного аналитического helper, без подмены `_cal_cost()` или сумм: после новых
15 минут duration/cost становятся **75**, а две существующие строки сохраняют
**amount=-60, unit_amount=1**. Их ID не меняются. Это контролируемая регрессия
порядка вызовов, а не исполнение Enterprise-модулей локально.

В прямом ручном `_compute_duration()` возможен ещё один путь: inverse
`_set_duration()` вызывает helper при присвоении duration. При штатном
stored-recompute этот путь может быть защищён. Поэтому одна только MRO не
описывает все runtime-вызовы. В пассивную трассу включён и `_set_duration()`.

Точные исходные различия на реальной Enterprise-базе предыдущий отчёт не
сохранил. Следующий staging-запуск покажет реальные значения и последовательность
с новым исправлением. Приёмка Enterprise и потока 4 PASS + 1 FAIL не объявляется.

## Исправление в адаптере

`native_analytic_sync.py` расширяет WO в нашем модуле; Enterprise-файлы не
изменяются. Во время настоящего `_compute_duration()` аналитические callback
управляемых WO откладываются, включая inverse-callback. После успешного super
штатный `_create_or_update_analytic_entry()` получает эти WO **один раз**.
Для смешанного recordset обычные WO продолжают поступать в helper сразу.
При исключении отложенный scope обязательно восстанавливается.

Формулы duration, стоимости оборудования/сотрудников и native helper сохранены.
Адаптер не создаёт/удаляет analytic lines и не назначает им amount вручную:
существующие связи обновляет штатная реализация. Первичные строки новой WO
создаются ею обычным способом. Регрессия подтверждает обновление **тех же ID**
до -75/1.25, один непустой native helper-вызов с duration=75 и отсутствие
изменений при повторном расчёте.

Область применения ограничена:

- современным управляемым QUNIT MO (`quality_unit_route_required`, без legacy
  transfer-control), а также MO с уже зафиксированным native partial revision;
- собственными свежими WO rollback-аудита через приватный Python scope.

Обычные MO не включаются в этот scope. Дополнительно проверяются фактическая
позиция наших двух wrappers в MRO и SHA обоих полученных Enterprise duration
методов. Community или неизвестная/изменённая duration-цепочка не включает
синхронизацию. Это **не** допуск `_cal_cost()`; его allowlist не изменяется.
Если другая загрузка модулей ставит свой wrapper выше нашего, scope остаётся
выключенным, а факт виден в `diagnostics.duration_sync` и MRO отчёта.

Checkpoint сохраняет проверку fingerprints: смена реализации учитывается как
смена basis. Старые капитализированные документы автоматически не переводятся
на новый source; их guard и запреты изменения интервалов не ослабляются.

## Сравнение и диагностика

Оба исходных аудиторских сценария оставляют прежнее число явных вызовов:
два в `stable_analytics`, один после снимка новой работы в `new_work`.
Подготовительных дополнительных пересчётов перед сравнением нет.

Снимок содержит исходные cost/duration/basis/MO/analytic данные и SELECT-снимки
сохранённых значений до/после чтения cost и basis. SELECT не вызывает flush,
инвалидацию кеша или ORM compute. Для строк/полей проверяются обычные ACL/rules;
повторный SQL-поиск также обнаруживает добавленные строки и связи. Ограничение
на ID только новых fixture-строк сохраняется.

Новые `state_comparisons` показывают:

- пары baseline→first, first→second и automatic→explicit;
- изменённые разделы и точные пути полей basis/duration/cost;
- исходные/новые ID, добавленные/удалённые строки и изменение полей **по ID**;
- amount, unit_amount и другие changed values, включая computed поля;
- изменения аналитических связей и сохранённых SQL-значений.

Ни одно исходное поле не исключено из equality, числового округления нет.
Снимки и результаты diff копируются, чтобы последующие изменения mutable
значений не меняли уже захваченный результат. Изменение только computed поля
или порядка строк также остаётся FAIL; причина становится видимой.

При оставшемся FAIL JSON `STATE_DIFF` включается непосредственно в `error`.
Поэтому тот же Scheduled Action, печатающий только ошибки проверок, покажет
различия без изменения своего кода. Структурированный diff есть и в check,
и в `report.diagnostics.state_comparisons`.

`native_call_traces` пассивно записывает вход/выход фактических функций,
provider, ID recordset и duration **из кеша**. Нативные методы не подменяются,
дополнительного чтения computed fields нет. Существующий profiler сохраняется;
невозможность трассировки отмечается явно как NOT_RUN/ERROR/TRUNCATED.
Все исходные 14 проверок и счётчики PASS/FAIL/NOT_RUN сохранены.

## Проверки и повторный запуск

- **70 Community ORM-тестов**, без failures/errors.
- **76 ORM-тестов** с настоящими `account_analytic_uom` и
  `mrp_subcontracting_account`, без failures/errors.
- Финально повторены **24** cost/analytic/UoM теста, включая вывод STATE_DIFF
  для неизменённого Scheduled Action. Это не дополнительные уникальные тесты.
- **206 автономных контрактов**; Python/XML, import моделей и safe_eval скриптов.
- Прежние `140+35=175`, `190=152+38`, неизвестные cost-провайдеры,
  неизменность booked intervals, атомарность и ACL-регрессии сохранены.

Проверка механизма pre-super использует тестовую подстановку только порядка
duration-callback и predicate синхронизации. Она не объявляется Enterprise
source/MRO-проверкой, не добавляет Enterprise `_cal_cost` и использует настоящие
ненулевые Community cost/analytic операции. Отдельно подтверждён отказ source
predicate на Community, rollback при ошибке post-duration helper и восстановление
callback scope после неё.

После доставки кода и перезапуска Python-службы повторить **тот же**
`enterprise_employee_cost_audit_scheduled_action.py`. Новых столбцов нет.
Проверить `duration_sync.reviewed_enterprise_duration_chain`, все 14 результатов,
state comparisons/trace, отсутствие изменения IDs и rollback COMPLETE.
Производственный Enterprise cost-провайдер остаётся заблокированным.

Журналы: `evidence/enterprise_analytic_sync_community_orm.txt`,
`evidence/enterprise_analytic_sync_subcontracting_orm.txt`,
`evidence/enterprise_analytic_sync_final_diagnostics_orm.txt`,
`evidence/enterprise_analytic_sync_portable.txt`.
