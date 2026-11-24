# Дополнительная приемка 17.0.1.0.3

24 ноября 2026, Europe/Kiev. Только текущая сессия Вадима Герасименко, uid64. Другие сессии/учетные записи не искались, действия за других согласующих не выполнялись. Все бизнес-записи TEST-VATAGA-APPROVALS.

**Основная матрица 82:73 PASS /0 FAIL /9 BLOCKED.** 71 исходный PASS принят как промежуточный; целиком не повторялся. B05/B06 закрыты в проверенном отрицательном объеме. B04/B09/D11 имеют частичные результаты, но не объявлены полным PASS. Полная приемка остается BLOCKED.

| Пункт | Статус | Фактический результат и предел проверки | Доказательство |
|---|---|---|---|
| 1 / B05–B06 | PASS | Minimum2: существующий товар 2022 и отсутствующий 2028 после собственного голоса остаются pending. Minimum1+другой required:2024/2029 остаются pending. Необходимость второго/обязательного голоса сохранена и при работе workflow_bridge. Другие пользователи не утверждали. | [Заявка 2028](https://vataga-staging.erpu.cloud/web#id=2028&model=approval.request&view_type=form&cids=1); [Заявка 2029](https://vataga-staging.erpu.cloud/web#id=2029&model=approval.request&view_type=form&cids=1) |
| 2 / B04 | BLOCKED | Частичный UI PASS: текущий пользователь второй, uid2 pending, uid64 waiting; кнопки Approve нет. Полное прохождение очереди и отрицательный native action_approve RPC не выполнялись. | [Заявка 2023](https://vataga-staging.erpu.cloud/web#id=2023&model=approval.request&view_type=form&cids=1) |
| 3 / B09 | BLOCKED | Частичный UI PASS: назначен только uid2, владелец/current uid64 может отправить/отменить, но не имеет кнопки/активности Approve. Прямой native RPC и отрицательная проверка обычной роли не доступны. | [Заявка 2025](https://vataga-staging.erpu.cloud/web#id=2025&model=approval.request&view_type=form&cids=1) |
| 4 / D11 | BLOCKED | Параллельный UI запуск PASS: Promise.all, оба клика начаты в 1795535619601 мс и завершены в 1795535619872 мс, без await первого перед вторым. Один успех/один серверный отказ; один 15145/P14896, одна строка 1 шт.,119.10UAH. Точное перекрытие HTTP/SQL не измерено: performance API не доступен в read-only evaluator. Последовательный replay не выдается за полную конкурентную проверку. | [Заявка 2027](https://vataga-staging.erpu.cloud/web#id=2027&model=approval.request&view_type=form&cids=1) |
| 5 / импорт/RPC | BLOCKED | В штатном cog menu My Requests доступны Export all/Knowledge/Spreadsheet, Import отсутствует; registry create=false. WebMCP не публикует RPC. Не создавались обходные меню/server actions/hooks и не менялся readonly DOM. Подготовлен native load regression для отдельной Enterprise БД; не исполнен. | [Заявка 2027](https://vataga-staging.erpu.cloud/web#id=2027&model=approval.request&view_type=form&cids=1) |
| 6 / D12 delete | PASS | Native unlink draft потребовал cancel. Только TEST15145 отменен и удален; linked list0. Реальный серверный вызов из сохраненной старой кнопки отклонен после удаления. Свежая заявка не имеет RFQ/create buttons, причина already formed сохраняется. Никакой отправки/подтверждения закупки. | [Заявка 2027](https://vataga-staging.erpu.cloud/web#id=2027&model=approval.request&view_type=form&cids=1) |
| 7 / контакт и получатель | PASS | 2021 содержит разные ContactA/recipientB. Поиск каждого возвращает 2021. Группировки на 14TEST-строках: ContactA1/1 шт.+None13/26 шт.; recipientB1/1 шт.+None13/26 шт. Добавлены поиск/группировка контакта. | [Заявка 2021](https://vataga-staging.erpu.cloud/web#id=2021&model=approval.request&view_type=form&cids=1) |
| 8 / производительность | PASS | В момент измерения 28 доступных позиций: три теплых UI открытия 259/303/284 мс. Массовое создание не использовалось. Это малый существующий объем; нет SQL/network профиля и гарантии скорости на тысячах строк. Позднее добавлены только целевые TEST-заявки. | Реестр 1159 |
| 9 / Date и Datetime | PASS | 2020 показывает 24.11.2026 и 30.11.2026 без 02:00. create_date сохраняет 16:39:49.10 клиентских assertions проверяютUTC−/UTC+/DST/empty. Только наш registry/реальный Date получает календарный widget; Datetime-ветка сохраняется native и проверена portable-контрактом. Живой source Datetime period в ERP-UA не подменялся. | [Заявка 2020](https://vataga-staging.erpu.cloud/web#id=2020&model=approval.request&view_type=form&cids=1) |
| 10 / 11 бизнес-решений | PASS | Все 11 решений повторно разобраны. Три варианта возврата в draft: текущая блокировка; редактирование с новой неизменяемой ревизией и полным approval; новый связанный черновик. Предлагается последний при допустимости нового номера. Бизнес-логика не менялась. | [Решения](BUSINESS_DECISIONS_RU.md) |
| 11 / UI/украинский | PASS | После Apps Upgrade build3950: у approved purchase2027 нет inputs темы/обеих пар периода и English Date Start/End; подписи Дата початку/Дата закінчення. Purchase draft2020 сохраняет editable тему/период 24–30.11. Обычная approved2019 сохраняет native editable тему/позднюю пару периода. Метаданные snapshot/type вgroup selector украинские, предупреждения мастера корректны. Широкий реестр использует native horizontal scroll/optional columns; кнопки не перекрыты. | [Заявка 2027](https://vataga-staging.erpu.cloud/web#id=2027&model=approval.request&view_type=form&cids=1) |
| 12 / публикация .3 | PASS | [55fa920f3](https://github.com/ERP-Ukraine/vataga/commit/55fa920f3fcbb4e48a57d503f021b89eeb83459f), [Actions485 success](https://github.com/ERP-Ukraine/vataga/actions/runs/45433683411), ERP-UA3950 running16:09:37UTC. Apps Upgrade SUCCESS, установленная 17.0.1.0.3. Targeted UI retest PASS; runtime исправления опубликованы. Финальный отчет публикуется отдельно без изменения кода/версии. | См. публикации ниже |

## Подтвержденные исправления

- P1: вкладка товаров приnative has_product=no; raw category_id при переходе из dashboard/мастера в товар/поставщика. .2 контекст-исправление было неполным, дефект повторно открыт и исправлен по фактическому native client merge.
- P2: календарные даты; ложные permission hints нового мастера; украинские labels собственных дополнительных полей; UI readonly существующей серверной политики, включая позднюю дублирующую ERP-UA пару периода.
- Контакт добавлен в поиск и отдельную группировку. SQL-типы проекций, данные старых заявок, статусы, права и политика возврата в draft не менялись.

## Публикации

- [4a59011c7](https://github.com/ERP-Ukraine/vataga/commit/4a59011c7a856fccbb9e2480605721504db7037e):Date/widget/вкладка/контакт, [Actions479](https://github.com/ERP-Ukraine/vataga/actions/runs/45425813588), ERP3942 running, Upgrade SUCCESS.
- [048a7ffc9](https://github.com/ERP-Ukraine/vataga/commit/048a7ffc9d8873413966b47c86c399309283b656):raw category_id/ACL hints/labels/tests. [Actions481](https://github.com/ERP-Ukraine/vataga/actions/runs/45429000561) failed/ERP3945 failed при внешнем XML-дефекте; код затем активирован 3947, Upgrade/retest PASS.
- [7feea67ed](https://github.com/ERP-Ukraine/vataga/commit/7feea67ed98fa0024693c9d797c5a1a79857428c):отдельный явно разрешенный однострочный fix ratel_support_embed. [Actions482](https://github.com/ERP-Ukraine/vataga/actions/runs/45430101026), ERP3947 running15:51:22UTC.
- [d19881df2](https://github.com/ERP-Ukraine/vataga/commit/d19881df2ead37a1cff3b3874b10f5c56af02958):UI readonly, [Actions484](https://github.com/ERP-Ukraine/vataga/actions/runs/45432804627), ERP3949 running16:05:24UTC. Upgrade успешен, затем выявлена поздняя пара периода.
- [55fa920f3](https://github.com/ERP-Ukraine/vataga/commit/55fa920f3fcbb4e48a57d503f021b89eeb83459f):последняя ERP-UA пара периода и регрессия настоящего механизма наследования Odoo.

[55fa920f3](https://github.com/ERP-Ukraine/vataga/commit/55fa920f3fcbb4e48a57d503f021b89eeb83459f), [Actions485 success](https://github.com/ERP-Ukraine/vataga/actions/runs/45433683411), ERP-UA3950 running16:09:37UTC. Apps Upgrade SUCCESS, установленная 17.0.1.0.3. Targeted UI retest PASS; runtime исправления опубликованы. Финальный отчет публикуется отдельно без изменения кода/версии.

## Внешний дефект среды

Внешний 6224d26 внес eval obj(...) безmodel. Нативный Community Odoo17 converter локально воспроизвелNameError; добавлениеmodel="ir.ui.menu" прошло ту же проверку. Actions480/481 завершалисьfailed, затем разрешенный 7feea67 восстановил build3947. Серверный traceback не был доступен; доказательства — XML/нативный converter и результат сборки. Чужая логика и версия manifest не менялись.

Автоматическая проверка первоначально отклонила повтор чужого job480. Он не был выполнен. Пользователь затем разрешил один повтор и отдельно минимальное XML-исправление; выбран отдельный исправляющий коммит. Последующие внешние 47d37c77a сохранены. Один push нашего UI-коммита был отклонен fetch-first и безопасно повторен через fetch/rebase; force не использовался.

## Автоматические проверки и оставшиеся ограничения

PASS:45portable Python,16URL,29Python/5XML/4JSsyntax,manifest/ACL/PO/Git diff.63Enterprise ORM/HTTP tests собраны, не исполнены. Новые проверки: native XML inheritance первого/последнего field, Date/Datetime arch, raw category client-merge, permission defaults, native import load. Три прежних PostgreSQL lock-сценария прошли; тело lock не менялось.

Реальный QUnit:15 тестов/185assertions,183PASS/2FAIL. Собственные 17 функциональных assertions PASS (calendar10+readonly7). Единственный failed test содержит только 2teardown-fail из-за neutralize banner/Cloudflare; он не объявлен общим PASS. Дополнительные 13 существующих stock_minimum_control/sale_demand client tests PASS. [Клиентский прогон](https://vataga-staging.erpu.cloud/web/tests?mod=web&filter=Vataga%3A). TransactionCase/HttpCase на общей staging не запускались.

Оставшиеся 9: B04,B07,B09,C14,D11,E14,E16,E17,F09. Полное прохождение других согласующих, manager, ограниченные ACL/rules/read/search/export/group/company и детерминированное перекрытие native RPC/SQL требуют другой роли или отдельной лицензированной среды. UI readonly и одновременное инициирование кликов не подменяют эти доказательства.

## Тестовые записи нового этапа

- [TEST-VATAGA-APPROVALS-20261124-MINIMUM2](https://vataga-staging.erpu.cloud/web#id=14&model=approval.category&view_type=form&cids=1) — approval.category/14.
- [TEST-VATAGA-APPROVALS-20261124-SEQUENCE2](https://vataga-staging.erpu.cloud/web#id=15&model=approval.category&view_type=form&cids=1) — approval.category/15.
- [TEST-VATAGA-APPROVALS-20261124-MIN2-NEGATIVE](https://vataga-staging.erpu.cloud/web#id=2021&model=approval.request&view_type=form&cids=1) — approval.request/2021.
- [TEST-VATAGA-APPROVALS-20261124-MIN2-ONE-VOTE](https://vataga-staging.erpu.cloud/web#id=2022&model=approval.request&view_type=form&cids=1) — approval.request/2022.
- [TEST-VATAGA-APPROVALS-20261124-SECOND-CANNOT-APPROVE](https://vataga-staging.erpu.cloud/web#id=2023&model=approval.request&view_type=form&cids=1) — approval.request/2023.
- [TEST-VATAGA-APPROVALS-20261124-REQUIRED](https://vataga-staging.erpu.cloud/web#id=16&model=approval.category&view_type=form&cids=1) — approval.category/16.
- [TEST-VATAGA-APPROVALS-20261124-UNASSIGNED](https://vataga-staging.erpu.cloud/web#id=17&model=approval.category&view_type=form&cids=1) — approval.category/17.
- [TEST-VATAGA-APPROVALS-20261124-REQUIRED-ONE-VOTE](https://vataga-staging.erpu.cloud/web#id=2024&model=approval.request&view_type=form&cids=1) — approval.request/2024.
- [TEST-VATAGA-APPROVALS-20261124-NOT-AN-APPROVER](https://vataga-staging.erpu.cloud/web#id=2025&model=approval.request&view_type=form&cids=1) — approval.request/2025.
- [TEST-VATAGA-APPROVALS-20261124-CONCURRENT-DELETE](https://vataga-staging.erpu.cloud/web#id=2026&model=approval.request&view_type=form&cids=1) — approval.request/2026.
- [TEST-VATAGA-APPROVALS-PRODUCT-D-CONCURRENT](https://vataga-staging.erpu.cloud/web#id=12092&model=product.product&view_type=form&cids=1) — product.product/12092.
- [TEST-VATAGA-APPROVALS-20261124-CONTEXT-DASHBOARD-RETEST](https://vataga-staging.erpu.cloud/web#id=2027&model=approval.request&view_type=form&cids=1) — approval.request/2027.
- [TEST-VATAGA-APPROVALS-20261124-MIN2-MISSING-GUARD](https://vataga-staging.erpu.cloud/web#id=2028&model=approval.request&view_type=form&cids=1) — approval.request/2028.
- [TEST-VATAGA-APPROVALS-20261124-REQUIRED-MISSING-GUARD](https://vataga-staging.erpu.cloud/web#id=2029&model=approval.request&view_type=form&cids=1) — approval.request/2029.
- [TEST-VATAGA-APPROVALS-PRODUCT-E-CONTEXT](https://vataga-staging.erpu.cloud/web#id=12093&model=product.product&view_type=form&cids=1) — product.product/12093.
- [P14896 / TEST-VATAGA-APPROVALS-CONTEXT-DASHBOARD-RETEST (DELETED)](https://vataga-staging.erpu.cloud/web#id=15145&model=purchase.order&view_type=form&cids=1) — purchase.order/15145 — удален намеренно после native cancel.

Новый supplierC:TEST-VATAGA-APPROVALS-VENDOR-C-CTX-RETEST,99.25UAH, связан сproduct12093. Его category tags не были навязаны из approval.category. TEST15145/P14896 удален; остальные рабочие закупки/документы не менялись. Письма/закупки не отправлялись, заказы/складские операции не подтверждались. Main/production/пароли/права не изменялись.

[Подробный анализ 11 решений и вариантыdraft](BUSINESS_DECISIONS_RU.md). До согласования остается текущая строгая политика. Перенос вmain/production не выполнялся и не предлагается до закрытия критических блокеров.
