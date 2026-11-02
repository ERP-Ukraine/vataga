# Исправление подготовки QC в Enterprise integration probe

Основа: `origin/staging`, `62d6696`; версия **17.0.2.62**.
Пользователь сообщил реальный результат: employee 14/14 PASS; none и lot
0/24, `Expected five identified real QC samples`; оба rollback COMPLETE.
Enterprise cost source/MRO подтверждены. Полный цикл ещё не пройден.

По исходникам `initialize_qc_unit_identity()` идентифицирует существующие
образцы. Сам по себе этот вызов не гарантирует создания матрицы. В fixture
теперь используется штатная последовательность:

1. Проверить фактическое `production.qty_producing == 5`, точные MO/WO/product
   источника QC и `x_control_stage == 'in_process'`.
2. Вызвать `_initialize_measurement_snapshot()`. Убедиться, что штатный snapshot
   включил `measurement_matrix_required`; прямой записи защищённого поля нет.
3. Вызвать `initialize_in_process_measurement_matrix()`. Требовать ровно пять
   образцов, штатную sample quantity 5 и отсутствие batch error.
4. Вызвать `initialize_qc_unit_identity()`. Требовать ровно пять различных
   `controlled_unit_id`.

Нативный snapshot сам может создать образцы, если количество уже известно.
Следующий штатный вызов генератора сохраняет существующие строки. Fixture
не создаёт образцы вручную, не подставляет frozen quantity и не обходит tokens,
ACL, проверки QUNIT/QC или стоимости. Производственные методы не менялись.

При ошибке `QC fixture stage=...` показывает preconditions/snapshot/matrix/identity.
JSON `QC_BATCH` включает ID MO/WO/QC, фактическое количество, источники QC,
этап контроля, признак matrix_required, ID образцов и точные ответы
`_get_in_process_sample_quantity()` и `_get_in_process_batch_error(validate_current=True)`.
Ошибка диагностического helper также возвращается явно. Исходное исключение
сохраняется как cause в traceback существующего integration report. Ошибка
подготовки остаётся FAIL с NOT_RUN, а не PASS какого-либо сценария.

Не изменены все 14 employee и 24 integration сценария для каждого tracking,
транзакция с обязательным rollback и ограниченный допуск Enterprise cost provider.
Обычные Enterprise MO по-прежнему не получают разрешение на выпуск.

Локально повторены 74 Community ORM-теста и 80 с настоящими
`mrp_subcontracting_account` и `account_analytic_uom`, без failures/errors.
Сохранён реальный Community результат 140+35=175. Пройдены 218 автономных
контрактов, включая шесть новых проверок последовательности fixture, отказа
при неверном источнике/количестве, диагностики пустой матрицы и неполной
идентификации. Эти шесть проверок проверяют orchestration через API doubles;
они не объявляются реальным Enterprise ORM-тестом. Проверены Python/XML,
импорт определений моделей и safe_eval существующих Scheduled Actions.

Логи: `evidence/enterprise_qc_fixture_community_orm.txt`,
`enterprise_qc_fixture_addons_orm.txt`, `enterprise_qc_fixture_portable.txt`.

После доставки исправления и перезапуска Python-службы staging повторить
**тот же** `scripts/production_loss_scheduled_action.py`. Ожидаем 14/14,
затем 24/24 для none и 24/24 для lot, без FAIL/NOT_RUN, rollback COMPLETE.
Полный Enterprise цикл 4+1 остаётся предметом этого запуска. Статус потерь
`manual_periodic_pending` не изменён; бухгалтерская проводка не создаётся.
main и production не затрагиваются.
