# Этап 13.3: rollback-only Enterprise-интеграция 4+1

Основа: staging `68304bf`, версия **17.0.2.62**. Пользователь сообщил успешный
реальный employee-аудит: AUDIT_PASS, 14 PASS, rollback COMPLETE,
reviewed_enterprise_duration_chain=True, cost_provider_authorized=False.
Это подтверждает предварительный аудит; **полный Enterprise Stock/QC цикл
этим сообщением ещё не подтверждён**.

Повторный реальный запуск на `62d6696`: employee audit 14/14 PASS;
integration none/lot остановились до сценариев (0/24) на подготовке образцов,
оба rollback COMPLETE; source/MRO подтверждены. Исправление fixture и порядок
повторного запуска описаны в `enterprise_qc_fixture_2026-11-02.md`.

## Граница допуска

Разрешается только фактически проверенная реализация:

```python
    def _cal_cost(self):
        return super()._cal_cost() + sum(self.time_ids.mapped('total_cost'))
```

- owner: `odoo.addons.mrp_workorder.models.mrp_workorder.MrpProductionWorkcenterLine`;
- implementation: тот же модуль, qualname `MrpProductionWorkcenterLine._cal_cost`;
- файл: `/mnt/enterprise-addons/mrp_workorder/models/mrp_workorder.py`;
- SHA256 исходного текста с отступами и завершающим newline:
  `71c4a9b3bfda60a38874a601ca26620730a9c121de75e030de7c13c593049d12`;
- ровно три определения в порядке фактического registry MRO:
  собственный `NativePartialWorkorder._cal_cost`, указанная Enterprise-функция,
  сохранённый объект штатного Community `mrp.MrpWorkorder._cal_cost`;
- отсутствие wrapper и дополнительных, в том числе затенённых, определений;
- прежний точный guard проверенной Enterprise duration-цепочки.

Допуск существует исключительно в приватном Python scope на **отдельном
cursor с обязательным rollback**, только на `odoo17-vataga-staging`. Требуется
обычный текущий администратор. Каждый новый fixture MO регистрируется явно:
ID выше максимального до fixture, create_uid текущего пользователя, доступная
компания, отсутствие предшествующего выпуска. Проверяется совпадение cursor,
UID и production ID при каждом обращении к cost basis.

Нет context/RPC-флага, сохранённого whitelist или глобального разрешения.
Обычные MO остаются заблокированы для Enterprise-выпуска. На Community ничего
дополнительно не разрешено. Неизвестный source/file/MRO блокирует проверку ещё
до создания fixture. Изменение расположения файла также требует нового review.
Проверки `_cal_price`, запрет субподрядных веток и checkpoint не ослаблены.

## Следующий Scheduled Action

После доставки кода в staging и перезапуска Python-службы выполнить
`scripts/production_loss_scheduled_action.py` от администратора в staging.
Версия остаётся `.62`, новых полей или миграции checkpoint нет.

Скрипт сначала повторяет **все 14** employee/analytic проверок без допуска
провайдера. Только AUDIT_PASS, 14/0/0 и COMPLETE rollback позволяют вызвать:

```python
env['mrp.production']._run_quality_production_loss_probe('odoo17-vataga-staging', tracking='none')
env['mrp.production']._run_quality_production_loss_probe('odoo17-vataga-staging', tracking='lot')
```

Каждый вызов имеет собственную транзакцию. Выполняются настоящие QC, QRUN,
поштучный маршрут, решение ТР, приход компонентов, MRP/Stock, FIFO SVL,
checkpoint и loss facts. Исключения возвращают stage и полный traceback.
При завершении rollback проверяется отсутствие собранных fixture ID в таблицах;
откатываются также записи chatter и очищаются postcommit callbacks. Глобальные
настройки, ACL, пользователи и существующие товары не меняются; sudo не нужен.

Подготовка времени явно синтетическая: три новые закрытые 20-минутные WO,
оборудование 60/час и отдельный временный сотрудник 30/час. `user_id=False`
исключает подстановку сотрудника администратора. Стоимость рассчитывают реальные
штатные методы: оборудование 60, труд 30, компоненты 100. Расчёт не подменяется.
Существующий analytic plan используется только для новых временных accounts.
Реальное время операторов на shop floor данным fixture не доказывается.

## Что должно быть в отчёте

Для **каждого** tracking (`none`, `lot`):

| Результат | Ожидание |
| --- | --- |
| result / rollback | PASS / COMPLETE |
| expected_checks / passed / failed / not_run | 24 / 24 / 0 / 0 |
| source_matches / reviewed_enterprise_duration_chain | True / True |
| admission / production_provider_authorized | rollback_fixture_only / False |
| plan / good_quantity / loss_quantity | 5 / 4 / 1 |
| component_cost / equipment_cost / employee_cost | 100 / 60 / 30 |
| total_cost / good_value / loss_value | 190 / 152 / 38 |
| MO / Backorder / Scrap | done / 0 / 0 |
| checkpoints | 3 настоящие строки, по 30; суммарно 90, реальные basis и timer IDs |
| FIFO SVL | расход компонентов −100, готовая продукция +152; FAIL не выпускался |
| физический остаток FG | 4, точные четыре PASS QUNIT; для lot сохранена партия |
| потери | 1 FAIL QUNIT, один native loss и один quality loss fact на 38 |
| accounting_status / gl_entries_created | manual_periodic_pending / 0 |

`cost_mro` показывает файл, SHA, порядок и approved=True **внутри fixture**.
`boundaries` содержит реальные ID и суммы до двух искусственных ошибок:
после Stock/SVL и после checkpoint/loss facts. Каждый отрицательный сценарий
сравнивает состояние после rollback с исходным. Ни один пропуск не даёт PASS.

Проверки 1–8: закрытый допуск до регистрации MO; отсутствие решения ТР;
пересечение good/loss; подмена FAIL на PASS; конфликтующий открытый Alert;
неполное потребление; rollback после Stock; rollback после checkpoint/facts.

Проверки 9–12: реальный результат 4+1/190=152+38; сохранённые checkpoint;
полная неизменность повторной операции и native button_mark_done; неизменность
после дополнительного native duration расчёта. Снимок берётся **до** последнего
расчёта: он не используется для сокрытия устаревшей аналитики.

Проверки 13–24: запрет write/unlink checkpoint; изменения loss fact;
изменения капитализированной duration, конца интервала, сотрудника,
employee_cost и удаления интервала; другой request; дополнительный выпуск FAIL;
изменение hourly_cost сотрудника и costs_hour оборудования с проверкой
настоящего сохранённого checkpoint.

`posted_evidence` содержит ID и значения движений/строк, SVL, quants, checkpoint,
loss/stock facts, WO duration/basis/cost, аналитических строк и их связей.
`comparisons` содержит точные различия, если любой повтор/rollback меняет эти
данные; округления или исключения computed полей нет. История QC/QRUN/execution
также проверяется на неизменность. Итог INTEGRATION:PASS требует оба tracking.

## Дальнейший учёт потерь

`manual_periodic_pending` означает доказанную производственную потерю и её
распределённую стоимость. **Бухгалтерское списание не завершено**: account.move
не создаётся. Следующий отдельный этап должен согласовать период и счета учёта,
отразить 38 в периодическом учёте со ссылкой на исходный loss fact/MO/Alert,
исключить повторное признание и сверить 190 = 152 + 38 с материалами и WO.
Не следует повторно списывать FG или создавать Scrap: пятое изделие на склад
никогда не приходовало. Автоматического GL-механизма и статуса «проведено»
в текущей версии нет; приёмка бухгалтерского отражения требуется отдельно.

## Локальная проверка и оставшаяся приёмка

- Community ORM: **74** проверки; с настоящими `mrp_subcontracting_account` и
  `account_analytic_uom`: **80**, без failures/errors. Сохранены 140+35=175,
  190=152+38, unknown-provider, checkpoint, rate/timer, ACL и rollback регрессии.
- **212** автономных контрактов, включая шесть проверок точного source/MRO.
  Они проверяют распознавание кода, а не эмулируют Enterprise ORM.
- Отдельная транзакция проверена реальными Community ORM-записями: rollback
  после успеха и исключения, отсутствие MO после него, callbacks не выполняются,
  scope восстановлен. В этих тестах меняется только имя одноразовой тестовой
  базы для проверки механизма транзакции; cost provider не разрешается.
- Python/XML parse, импорт shipped model definitions, safe_eval Scheduled Actions.
- Логи: `evidence/enterprise_4plus1_community_orm.txt`,
  `enterprise_4plus1_addons_orm.txt`, `enterprise_4plus1_portable.txt`.

Прямого доступа к реальному Enterprise ORM здесь нет. Этот документ и скрипт
готовят следующий запуск; **результат Enterprise 4+1 ещё ожидается**. Выпуск в
обычных Enterprise MO и этап 13.3 не объявляются готовыми до проверки отчёта
полного цикла и последующей приёмки. main и production не затрагиваются.
