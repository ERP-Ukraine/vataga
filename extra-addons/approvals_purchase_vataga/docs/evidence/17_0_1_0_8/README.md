# Доказательства целевой приемки .8

Дата27.11.2026, `odoo17-vataga-staging`, текущая внутренняя браузерная сессия uid64. Только специально созданные TEST-записи. Это browser UAT/ручные вызовы через собственное серверное TEST-действие, не Enterprise TransactionCase/HttpCase. В текстовых DOM exports код редактора может повторяться перед фактическим результатом; результат расположен в последнем диалоге UserError. `negative-cases` завершался UserError: все пробные изменения, включая техническую lock_revision, откатились целиком.

- [Счетчики при открытом редакторе](MOD-open-counts.txt), [Cancel](MOD-cancel-counts.txt), [X](MOD-X-counts.txt), [Escape](MOD-Escape-counts.txt): одинаковые2 product IDs и2 supplierinfo IDs, строка пуста, аудит не установлен.
- [Окончательная UOM и цена в БД](UOM01-final-database.txt), [откат несовместимой UOM](UOM04-rollback-database.txt).
- [Точное сравнение исходных request/order/lines/audit до/после копий](original-copy-compare.txt), [сами копии](DUP-original-after-and-copies.txt).
- [Пустые операционные связи копий](DUP06-no-operational-relations.txt), [фактические native copy=False invoices/pickings/advance move lines](DUP06-operational-copy-contract.txt). payment_term/picking_type — штатные настройки, они закономерно copy=True.
- [Повторные проверки9 условий поиска](REG06-search-operators-retest.txt), [домены всех групп и текущий объем](REG06-all-group-domains-and-volume.txt), [старый удаленный собственный TEST](REG05-existing-deleted-test.txt).
- [Штатный XLSX12 строк](registry-v8.xlsx), [проверенные значения XML ячеек](export-verified.json). Номера, количества, пустые значения и UOM «м» машинно сверены; поздняя CON01 отражена в финальном экспорте. Цены не подменялись. Форматирование XLSX не редактировалось.
- [12 серверных отказов текущей сессии](SEC01-current-session-negative-cases.txt): не заменяют приемку под другой ролью.
- [Generated mobile kanban](UI01-generated-mobile-arch.txt): метод один, новая подпись есть, старой нет. [Ограничение responsive runtime](MOB01-viewport-blocked.txt): ширина фактически не сменилась; мобильный PASS не заявлен.
- [CON01 до](CON01-before.txt), [вкладка A](CON01-tab-A.txt), [вкладка B](CON01-tab-B.txt), [после](CON01-after.txt), [граница доказательства](CON01-status.txt):4/4→5/5, B12117/12222, A штатно отклонена; реальное перекрытие транзакций не подтверждено, BLOCKED.
- [Итоговые TEST-записи и installed version](final-runtime.txt), [машиночитаемая матрица](acceptance.json).
- Финальный guard retest: [4 прямых отклонения из new](TOP01-direct-approve-retest.txt), [положительные auto/manual/nonpurchase](TOP02-final-native-workflow-retest.txt), [readonly legacy цикл](TOP03-legacy-return-retest.txt), [проверка отсутствия сохраненных изменений истории](TOP03-no-persisted-history-change.txt), [VPR header реально старого собственного TEST](LINK01-manual-vpr-header.txt). Все диагностические создаваемые/изменяемые записи этого этапа завершены UserError и откатились целиком.

Снимки находятся в `docs/screenshots/v8-*`. Они оригинальные JPEG внутреннего браузера, не ретушировались; каждый отражает свой шаг UAT. В частности снимок реестра сделан до позднего заполнения строки97 в CON01. Raw результаты ранних неуспешных проверок сохранены локально в `.cache/approvals-review-evidence/v8`, а итоговый статус учитывает только повторные проверки после исправлений.
