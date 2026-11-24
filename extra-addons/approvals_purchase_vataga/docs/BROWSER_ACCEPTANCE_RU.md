# Браузерная приемка approvals_purchase_vataga на staging

Дата: 24 ноября 2026, Europe/Kiev. Среда: vataga-staging.erpu.cloud / odoo17-vataga-staging.

**Приемка продолжается. Полная готовность не подтверждена.** Исходники: 17.0.1.0.2; последняя подтвержденная установка: 17.0.1.0.1. Матрица: 48 PASS / 1 FAIL / 33 BLOCKED. BLOCKED включает еще не выполненные сценарии с описанной причиной.

Только текущая сессия Вадима Герасименко, uid=64. Другие роли/сессии не ищутся по уточнению пользователя. Расширенная сессия не подтверждает разграничение минимальных прав. Пароль, группы и права не менялись. На общей staging TransactionCase/HttpCase не запускались. WebMCP/Browser RPC инструменты не опубликованы; проверки выполняются настоящими UI-действиями.

## Публикации и установка

| Commit | Actions | ERP-UA | Apps |
|---|---|---|---|
| [bd0afc2f8](https://github.com/ERP-Ukraine/vataga/commit/bd0afc2f80c1c62c1b9cc295c808cd98a99e6607) | [success](https://github.com/ERP-Ukraine/vataga/actions/runs/45403701573) | Build #3936: running (2026-11-24T13:29:14Z) | Install SUCCESS через Apps, 17.0.1.0.0 |
| [435d4735d](https://github.com/ERP-Ukraine/vataga/commit/435d4735dd56071eb81fb3a7c2373cfba440440d) | [success](https://github.com/ERP-Ukraine/vataga/actions/runs/45406209129) | Build #3937: running (2026-11-24T13:43:42Z) | SUCCESS via browser |

## Дефекты и исправления

| ID | Приоритет | Факт и причина | Исправление | Повторная проверка |
|---|---|---|---|---|
| STG-001 | P1 | У новой purchase категории native has_quantity=no. Базовая таблица скрывает quantity, ввести количество невозможно. approvals.approval_product_line_view_tree: quantity column_invisible="parent.has_quantity == 'no'"; purchase UI запрещает менять header has_quantity. | Purchase-only условие видимости quantity; native nonpurchase условие сохранено. Версия 17.0.1.0.1 | PASS на 17.0.1.0.1 после Apps Upgrade |
| STG-002 | P2 | Поле мастера называется Mode на украинском UI. У Selection mode отсутствовал string. | Спосіб заповнення. Версия 17.0.1.0.1 | PASS на 17.0.1.0.1 после Apps Upgrade |
| STG-003 | P1 | После Заповнити та відкрити товар quick-create нового поставщика падает: res.partner.category(12) не существует. Возвращенный product action не содержал собственного изолированного context; category12 заявки интерпретируется как тег контакта. | Явный product context с locale/allowed companies и active product IDs; native get_formview_action. Request/wizard defaults и view hints не передаются. Версия 17.0.1.0.2 | PENDING на 17.0.1.0.2 |

## Матрица сценариев

| ID | Ожидаемое поведение | Роль | Фактический результат | Статус | Документ |
|---|---|---|---|---|---|
| A01 | Создание заявки с существующим товаром без URL. | Вадим Герасименко, uid=64, только текущая сессия | В заявке 2016 выбран уже существующий тестовый товар, URL строки очищен. Native confirm и approve успешны, количество 3 шт. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| A02 | Создание позиции без выбранного товара. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Создана и сохранена строка без product_id; затем заявка согласована с пустым товаром. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A03 | Сохранение полностью незаполненной позиции в черновике. | Вадим Герасименко, uid=64, только текущая сессия | После upgrade сохранена новая строка с пустыми product/description/URL и quantity=0. Черновик успешно записан, id=2016. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| A04 | Запрет отправки заявки без описания отсутствующего товара. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | При наличии HTTPS URL и пустом описании отправка отклонена: «Укажіть опис товару». | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A05 | Запрет отправки без обязательного URL. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | При заполненном описании и пустом URL отправка отклонена: «Укажіть коректний URL з протоколом HTTP або HTTPS». | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A06 | Запрет нулевого и отрицательного количества. | Вадим Герасименко, uid=64, только текущая сессия | Отдельно введены 0 и -2 при валидных описании/URL. Оба confirm отклонены: «Кількість має бути більшою за нуль». | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| A07 | Проверку некорректных URL. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | javascript:alert(1) сохранен только в черновике как неактивный текст; отправка отклонена украинским сообщением об URL. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A08 | Автоматическое заполнение владельца заявки. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Власник запиту и Ініціатор автоматически равны Вадиму Герасименко, res.users/64. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A09 | Недоступность редактирования владельца. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Власник запиту представлен readonly ссылкой, поля редактирования владельца нет. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A10 | Расположение колонки URL после описания. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Фактический порядок колонок: Товари, Опис, URL, Одиниця вимірювання. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| A11 | Открытие URL в новой вкладке браузера. | не проверена | Еще не выполнено | BLOCKED | — |
| A12 | Создание смешанной заявки с существующими и отсутствующими товарами. | Вадим Герасименко, uid=64, текущая сессия | Смешанная заявка сохранена: существующий товар 2 шт., отсутствующий для выбора существующего 5 шт., отсутствующий для создания 7 шт. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2017&model=approval.request&view_type=form&cids=1) |
| B01 | Согласование заявки с существующими товарами. | Вадим Герасименко, uid=64, только текущая сессия | Native new → pending → approved для существующего товара без URL, 3 шт. Без RPC_ERROR. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| B02 | Согласование заявки без выбранных товаров. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Нажаты native Розглянути и Затвердити: new → pending → approved при пустом product_id, без RPC_ERROR. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| B03 | Согласование смешанной заявки. | Вадим Герасименко, uid=64, текущая сессия | Native confirm/approve смешанной заявки успешны, все три строки и согласованные количества сохранены. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2017&model=approval.request&view_type=form&cids=1) |
| B04 | Последовательное многоэтапное согласование. | не проверена | Еще не выполнено | BLOCKED | — |
| B05 | Обязательных согласующих. | не проверена | Еще не выполнено | BLOCKED | — |
| B06 | Минимальное количество согласований. | не проверена | Еще не выполнено | BLOCKED | — |
| B07 | Согласование руководителем. | не проверена | Еще не выполнено | BLOCKED | — |
| B08 | Стандартные активности, уведомления и историю согласования. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | После отправки создана активность «Схвалення для Вадим Герасименко»; после approval она завершена, в chatter есть оба перехода и «Схвалення завершено». Внешние письма отключены staging. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| B09 | Запрет согласования пользователем без необходимых полномочий. | не проверена | Еще не выполнено | BLOCKED | — |
| B10 | Отсутствие автоматически созданных товаров после согласования. | Вадим Герасименко, uid=64, текущая сессия | До и после approval поиск TEST-VATAGA-APPROVALS в каталоге показывал ровно 1 ранее явно созданный товар. Обе missing строки оставались пустыми до действий мастера. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2017&model=approval.request&view_type=form&cids=1) |
| B11 | Блокировку товарных позиций во время согласования. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | В pending таблица стала readonly; Додати рядок и Видалити рядок исчезли. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| B12 | Штатные действия отказа, отмены, отзыва и возврата в черновик. | Вадим Герасименко, uid=64, только текущая сессия | Реально выполнены approved → pending (Зняти) → refused → cancel → new (Назад до чернетки). Chatter сохранил переходы, данные и lock остались. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| C01 | Наличие кнопки «Заповнити товар». | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | В approved строке без товара появилась кнопка Заповнити товар. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| C02 | Работоспособность кнопки внутри readonly-таблицы. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | Кнопка в readonly таблице реально открыла TransientModel мастер с согласованными описанием и URL. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| C03 | Выбор существующего товара. | Вадим Герасименко, uid=64, текущая сессия | В строке EXISTING-FILL через мастер выбран ранее созданный товар A; согласованные описание/URL и 5 шт. сохранены, UOM заполнена шт. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2017&model=approval.request&view_type=form&cids=1) |
| C04 | Создание нового товара на основании согласованного описания и URL. | Вадим Герасименко, uid=64, только текущая сессия | Нажато создание нового товара в мастере. Товар TEST-VATAGA-APPROVALS-C04-DENIED создан штатным ORM и подставлен в строку. Суффикс DENIED был именем проверки; действие фактически разрешено текущими правами. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| C05 | Перенос URL в карточку нового товара. | Вадим Герасименко, uid=64, только текущая сессия | На вкладке Купівля карточки product.template/13057 поле URL закупівлі равно согласованному https://example.com/TEST-VATAGA-APPROVALS. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=13057&model=product.template&view_type=form&cids=1) |
| C06 | Отметку заполнения после согласования. | не проверена | Еще не выполнено | BLOCKED | — |
| C07 | Отметку создания товара специально для заявки. | не проверена | Еще не выполнено | BLOCKED | — |
| C08 | Сохранение согласованного описания, количества и URL. | Вадим Герасименко, uid=64, только текущая сессия | После явного создания и заполнения товара описание TEST-VATAGA-APPROVALS-відсутній товар, URL и 1 шт. сохранены; UOM автоматически шт. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| C09 | Запрет изменения ранее согласованных товаров. | Вадим Герасименко, uid=64, только текущая сессия | После согласования и возврата в draft product/description остались readonly; новых товаров выбрать нельзя через UI. Прямой RPC отдельно не выполнен. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| C10 | Запрет изменения количества и единицы измерения вручную. | Вадим Герасименко, uid=64, только текущая сессия | Double click quantity после возвращения ранее approved в draft не открыл редактор; 0 textbox в таблице, 3 шт. сохранены. UI-проверка; прямой RPC не выполнен. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| C11 | Запрет добавления новых строк. | Вадим Герасименко, uid=64, только текущая сессия | Кнопка Додати рядок отсутствует в approved и после cancel/draft. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| C12 | Запрет удаления существующих строк. | Вадим Герасименко, uid=64, только текущая сессия | Кнопки Видалити рядок отсутствуют в approved и после cancel/draft. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| C13 | Запрет повторной замены заполненного товара. | Вадим Герасименко, uid=64, только текущая сессия | После заполнения товара кнопка Заповнити товар исчезла, строка осталась readonly. UI-проверка; прямой RPC не выполнен. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| C14 | Недоступность заполнения обычному пользователю. | не проверена | Еще не выполнено | BLOCKED | — |
| C15 | Возможность открытия карточки товара и заполнения поставщика. | Вадим Герасименко, uid=64 | Карточка из мастера открывается, но quick-create поставщика B вызывает MissingError res.partner.category(12). Из каталога товара A поставщик создан успешно. STG-003, исправление .2 подготовлено. | FAIL | [Открыть](https://vataga-staging.erpu.cloud/web#id=12090&model=product.product&view_type=form&cids=1) |
| C16 | Корректность фактических прав закупщика ERP-UA. | Вадим Герасименко, uid=64, текущая сессия | С текущими правами реально созданы товары A/B и supplierinfo для поставщика A с ценой 125,50 UAH. Группы, права и пароль не изменялись. Ошибка поставщика B относится к контексту, не к ACL. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=5756&model=res.partner&view_type=form&cids=1) |
| D01 | Недоступность создания ЗНКП при отсутствии товара. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | В approved заявке с отсутствующим товаром кнопка RFQ отсутствует, причина: «Не для всіх позицій вибрано товар». | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| D02 | Недоступность создания ЗНКП при отсутствии поставщика. | Вадим Герасименко, uid=64, только текущая сессия | После заполнения товаром без seller RFQ-кнопки нет: «Не для всіх товарів визначено постачальника». Supplier helper не вызвал RPC_ERROR. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| D03 | Доступность кнопки после заполнения всех товаров и поставщиков. | Вадим Герасименко, uid=64, текущая сессия | После сохранения поставщика A форма перечитана; появилась native кнопка Створити ЗНКП. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| D04 | Успешное создание ЗНКП для одного поставщика. | Вадим Герасименко, uid=64, текущая сессия | Создан native RFQ P14893 для тестового supplier A, 3 шт.; ни отправка почты, ни подтверждение заказа не выполнялись. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| D05 | Группирование товаров по нескольким поставщикам. | не проверена | Еще не выполнено | BLOCKED | — |
| D06 | Корректность количества, единиц измерения, цен и связей. | Вадим Герасименко, uid=64, текущая сессия | RFQ: 3 шт. × 125,50 = 376,50 UAH, VAT20=75,30, total451,80. После второй заявки та же строка 4 шт. ×125,50=502,00, total602,40. Native picking type311, ссылки обеих заявок ведут в один RFQ. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| D07 | Сохранение стандартной логики использования существующих черновиков закупок. Для этого используй только специально созданные тестовые документы и поставщиков, чтобы не изменить рабочие ЗНКП. | Вадим Герасименко, uid=64, текущая сессия | Только тестовые заявки 2016 и2015 использовали один тестовый draft P14893: количество увеличилось 3→4, origin содержит обе TEST-заявки. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| D08 | Защиту от повторного нажатия кнопки. | не проверена | Еще не выполнено | BLOCKED | — |
| D09 | Отсутствие дублирования закупочных строк. | Вадим Герасименко, uid=64, текущая сессия | После двух заявок в тестовом RFQ ровно одна товарная строка на 4 шт., дубль строки не появился. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| D10 | Отсутствие повторного увеличения количества. | не проверена | Еще не выполнено | BLOCKED | — |
| D11 | Защиту при одновременных вызовах, если доступны две независимые авторизованные сессии. | не проверена | Еще не выполнено | BLOCKED | — |
| D12 | Поведение после отмены или удаления специально созданного тестового ЗНКП. | Вадим Герасименко, uid=64, текущая сессия | Только тестовый P14893 отменен, не удален. После свежего открытия заявки RFQ-кнопки нет, причина ЗНКП вже сформовано, исходная строка1шт. сохранена. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| D13 | Возможность корректной повторной попытки после ошибки формирования. | не проверена | Еще не выполнено | BLOCKED | — |
| E01 | Наличие отдельного пункта меню. | Вадим Герасименко, текущая сессия (не доказательство минимальных прав) | В меню Затвердження присутствует отдельный Реєстр заявок на закупівлю. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| E02 | Отображение каждой товарной позиции отдельной строкой. | не проверена | Еще не выполнено | BLOCKED | — |
| E03 | Наличие всех колонок из ТЗ. | не проверена | Еще не выполнено | BLOCKED | — |
| E04 | Отображение существующих и отсутствующих товаров. | Вадим Герасименко, uid=64, только текущая сессия | Реестр реально открылся: были строки с существующими товарами и тестовая строка без товара. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| E05 | Отображение заявок разных закупочных категорий. | Вадим Герасименко, uid=64, только текущая сессия | В реестре одновременно отображались native Створити закупівлю и новая TEST-VATAGA-APPROVALS purchase-категория. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| E06 | Автоматическое включение новой тестовой категории типа purchase. | Вадим Герасименко, uid=64, только текущая сессия | Категория 12 появилась в реестре автоматически; по описанию найдена тестовая строка 17, id категории нигде не добавлялся в список. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) |
| E07 | Отсутствие заявок других типов. | не проверена | Еще не выполнено | BLOCKED | — |
| E08 | Кликабельность URL. | не проверена | Еще не выполнено | BLOCKED | — |
| E09 | Переход к исходной заявке. | не проверена | Еще не выполнено | BLOCKED | — |
| E10 | Поиск по товару, описанию, теме и номеру. | не проверена | Еще не выполнено | BLOCKED | — |
| E11 | Все предусмотренные фильтры. | не проверена | Еще не выполнено | BLOCKED | — |
| E12 | Все предусмотренные группировки. | не проверена | Еще не выполнено | BLOCKED | — |
| E13 | Отображение отметок заполнения и создания товара. | не проверена | Еще не выполнено | BLOCKED | — |
| E14 | Просмотр только собственных заявок пользователем с минимальными правами. | не проверена | Еще не выполнено | BLOCKED | — |
| E15 | Просмотр общего реестра уполномоченным пользователем. | не проверена | Еще не выполнено | BLOCKED | — |
| E16 | Отсутствие утечки данных чужих заявок через поиск, экспорт и группировку. | не проверена | Еще не выполнено | BLOCKED | — |
| E17 | Соблюдение ограничений компаний. | не проверена | Еще не выполнено | BLOCKED | — |
| E18 | Корректность отображения дат и периодов. | не проверена | Еще не выполнено | BLOCKED | — |
| E19 | Недоступность непосредственного редактирования реестра. | Вадим Герасименко, uid=64, только текущая сессия | Таблица/форма реестра readonly: нет create/edit/delete, описание и количество показаны текстом. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=17&menu_id=745&action=1159&model=approval.product.line&view_type=form) |
| F01 | Штатное поведение сохранено | не проверена | Еще не выполнено | BLOCKED | — |
| F02 | Штатное поведение сохранено | Вадим Герасименко, uid=64, только текущая сессия | Стандартный native confirm/approve существующего товара без URL отработал с количеством 3 шт. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |
| F03 | Штатное поведение сохранено | не проверена | Еще не выполнено | BLOCKED | — |
| F04 | Штатное поведение сохранено | не проверена | Еще не выполнено | BLOCKED | — |
| F05 | Штатное поведение сохранено | Вадим Герасименко, uid=64, текущая сессия | Реальная цепочка approvals_purchase_stock создала RFQ со штатным picking type311 и корректной UOM/количеством. Расширение не заменялось. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| F06 | Штатное поведение сохранено | не проверена | Еще не выполнено | BLOCKED | — |
| F07 | Штатное поведение сохранено | Вадим Герасименко, uid=64, текущая сессия | Стандартная закупка открывается, использует существующий тестовый draft, считает цену/налог и штатно отменяется. Заказ не подтверждался, складских операций не выполнялось. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) |
| F08 | Штатное поведение сохранено | Вадим Герасименко, uid=64, только текущая сессия | Каталог товаров открылся. Новый товар из мастера доступен обычной карточкой, purchase_ok=True, URL перенесен; существующие товары не менялись. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=13057&model=product.template&view_type=form&cids=1) |
| F09 | Штатное поведение сохранено | не проверена | Еще не выполнено | BLOCKED | — |
| F10 | Штатное поведение сохранено | Вадим Герасименко, uid=64, только текущая сессия | Native активность и сообщения о confirm/approve/withdraw/refuse/cancel сохранены. Письма neutralized staging. | PASS | [Открыть](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) |

## Тестовые записи

- [TEST-VATAGA-APPROVALS-20261124-PURCHASE](https://vataga-staging.erpu.cloud/web#cids=1&menu_id=388&action=606&model=approval.category&view_type=form&id=12) — approval.category/12 .
- [TEST-VATAGA-APPROVALS-20261124-A03-BLANK](https://vataga-staging.erpu.cloud/web#id=2015&model=approval.request&view_type=form&cids=1) — approval.request/2015 .
- [TEST-VATAGA-APPROVALS-20261124-A06-QUANTITY](https://vataga-staging.erpu.cloud/web#id=2016&model=approval.request&view_type=form&cids=1) — approval.request/2016 .
- [TEST-VATAGA-APPROVALS-C04-DENIED](https://vataga-staging.erpu.cloud/web#id=13057&model=product.template&view_type=form&cids=1) — product.template/13057 .
- [TEST-VATAGA-APPROVALS-20261124-A12-MIXED](https://vataga-staging.erpu.cloud/web#id=2017&model=approval.request&view_type=form&cids=1) — approval.request/2017 .
- [TEST-VATAGA-APPROVALS-PRODUCT-B-20261124](https://vataga-staging.erpu.cloud/web#id=12090&model=product.product&view_type=form&cids=1) — product.product/12090 .
- [TEST-VATAGA-APPROVALS-VENDOR-A-20261124](https://vataga-staging.erpu.cloud/web#id=5756&model=res.partner&view_type=form&cids=1) — res.partner/5756 .
- [P14893 / TEST-VATAGA-APPROVALS-RFQ-A-20261124](https://vataga-staging.erpu.cloud/web#id=15142&model=purchase.order&view_type=form&cids=1) — purchase.order/15142 cancel.

Штатные номера RFQ сохранены. Тестовые закупки однозначно отмечены TEST-VATAGA-APPROVALS в поставщике, origin и supplier reference. Поставщикам ничего не отправлялось, заказы и складские операции не подтверждались. Существующие рабочие документы не изменялись.

## Проверки и ограничения

Локально PASS: 41 portable Python,16 URL cases,29 Python/5 XML/3 JS syntax,manifest/ACL/PO/Git diff. 60 Enterprise ORM/HTTP tests собраны, не исполнены. QUnit еще не исполнен. SQL lock-body не менялось после трех прошедших PostgreSQL-сценариев.

Создание товаров и supplierinfo фактически доступно текущему uid64. Ранние несформировавшиеся UI-подсказки не являются доказательством отсутствия ACL.

Прямые negative RPC/import проверки, несколько независимых ролей и компаний не объявляются пройденными по одному лишь readonly UI.

## Внешние изменения среды

{'actual': 'При добавлении supplier каталог потерял соединение, после reload HTTP 404.', 'concurrent_commit': '84d1907b39cefef2864ebedbd5e9db36433d1b6d', 'concurrent_actions': 'https://github.com/ERP-Ukraine/vataga/actions/runs/45410136707', 'cause_status': 'Параллельно другой автор опубликовал ratel_support_embed; фактическая причина 404 пока не установлена.', 'recovery': 'Odoo восстановился после параллельного build3938. Тестовый supplier A затем создан и сохранен. Проблема контекста B воспроизведена уже при доступном сервере.'}

Самостоятельные бизнес-решения перечислены отдельно в CODE_REVIEW_RU.md. В ходе браузерных исправлений новые бизнес-ограничения не вводились.
