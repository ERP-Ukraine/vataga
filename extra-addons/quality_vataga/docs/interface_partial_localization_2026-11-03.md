# Виправлення інтерфейсу, фактичної обробки та українських повідомлень

Дата: 3 листопада 2026 року. Основа: `5372677806c6b84820b583f2e79a4c5910b383be`, `origin/staging`.
Робоча копія: `C:/Users/vadym/Desktop/vataga/output/quality-partial-release`, гілка `staging`.
Основна робоча копія містить стару, розбіжну `staging`; її файли та `main` не змінювалися.
Коміт цього виправлення містить лише `extra-addons/quality_vataga/`.

## Реалізована поведінка

Для міжопераційної перевірки приховані обидві стандартні кнопки `do_alert`;
їхні початкові умови видимості для вхідного та інших етапів збережені.
Залишається одна кнопка «Створити сповіщення» за наявності актуальних
невідповідностей. Старий виклик `do_alert` також спрямовується до цієї дії
лише для міжопераційного етапу.

Дія повертає словник відкриття стандартної форми без читання запису
`ir.actions.act_window`. Звичайні права інспектора та правила доступу до
перевірок, сповіщень і вкладень зберігаються; нових прав або `sudo()` немає.
Відкриття й скасування форми нічого не створюють. Збереження й надалі
обчислює актуальні невідповідності на сервері та переносить їх у сповіщення.

Повторне відкриття тієї самої невідповідності відкриває вже збережений запис.
Ключ запиту походить від ідентифікаторів рядків і фактичних значень,
незалежно від мови користувача. Унікальне обмеження бази захищає одночасні
збереження двох відкритих форм. Ключ недоступний для ручної зміни. Навмисне
створення незалежних сповіщень стандартним способом збережено; захист
стосується запитів кнопки матриці. Історичні сповіщення не переписуються
та не об’єднуються заднім числом.

Для плану 5 і фактичної кількості 3 матриця та ідентифікація використовують
наявну логіку фактичної партії: три зразки й три вироби. Кнопка завершення
робочого завдання підтверджує їхню обробку через наявний захищений механізм
часткового виконання та зупиняє штатний таймер. Завдання залишається відкритим:
оброблено 3, залишилося 2. Повторне підтвердження не дублює історію.
Це діє також для ідентифікованої міжопераційної партії без обов’язкового
поштучного режиму; історичний маршрут передач зберігає окрему поведінку.

`_check_quality_native_finish()` продовжує захищати закриття всього робочого
завдання. Виправлено вибір шляху перед цим методом, а не вилучено перевірки
всієї партії. Механізм визначення виробів приймає явний вибір або точні
зареєстровані ідентичності перевірки / попередньої операції; кількість сама
по собі не створює ідентичностей.

Підтвердження фізичної обробки не є дозволом якості. Непридатні та ще не
перевірені вироби, відсутні допуски, обов’язкові точки контролю, невідповідні
запуски операцій та відкриті утримуючі сповіщення продовжують блокувати
просування відповідних виробів. Власні негативні результати можуть
зберігатися разом із фактом обробки, як і до виправлення, без допуску далі.

Складських рухів і витрати компонентів підтвердження обробки не створює.
Серверні обмеження `quality_full_release.py` та `quality_partial_release.py`
не послаблені: їхні нетекстові дерева Python збігаються з основою.
Для обов’язкового поштучного маршруту випуск 3 із 5 цим виправленням **не
відкрито**. Він потребує окремого перевіреного механізму виробництва й складу.
Залишок залишається незавершеним, а два невиготовлені вироби не створюються.
Звичайний штатний випуск без цього обмеження використовує майстер замовлення
на залишок Odoo; це перевірено реальним Community ORM.

Українські тексти охоплюють помилки, підказки, назви дій, майстри,
результати матриці, повідомлення історії та діагностичні повідомлення.
Внутрішні назви моделей, полів, методів, значення `pass` / `fail` / `pending`,
префікси номерів `QUNIT/…` / `QRUN/…`, технічні результати автоматичних
діагностик і фактичні номери документів збережені. Старі незмінні записи
історії та знімки доказів не перекладаються заднім числом.

## Перевірки

| Рівень | Результат |
|---|---|
| Автономні контракти | **233 тести, успішно** |
| Реальний Community MRP/Stock ORM | **74 виконані тести, 0 failures/errors** |
| Синтаксис Python | **147 файлів, успішно** |
| Розбір XML | **39 файлів, успішно** |
| Синтаксис JavaScript | **4 файли, успішно** |
| Фактичний метод підписів JavaScript | **4 перевірки, успішно** |
| `git diff --check` | **Успішно для файлів цього виправлення** |
| Enterprise ORM якості, прав інспектора та робочих завдань | **НЕ ВИКОНАНО** |
| Повний браузерний набір Odoo QUnit та ручна перевірка інтерфейсу | **НЕ ВИКОНАНО** |

Enterprise-модулів `quality_control`, `quality_mrp`, `mrp_workorder` локально
немає; Docker daemon недоступний. Автономні перевірки дії та спадкування
кнопок не є підтвердженням Enterprise ACL або остаточно зібраних форм.
Регресії для цього середовища додані до `test_quality_check_alert_action.py`
та `test_quality_partial_workorder.py`, але не зараховані як виконані.

Community-прогін: офіційний локальний Odoo 17, Python 3.12,
окремий PostgreSQL 18 на `127.0.0.1:55434`. Runner створив і видалив власну
випадкову базу `qunit_partial_0cde6f78e9d84493`; жодної наявної прикладної
бази не використовував. Перевірено реальні рухи, залишки, оцінку запасів,
витрату компонентів, замовлення на залишок, відкат і паралельні транзакції.
Очікувана помилка серіалізації в тесті паралельного випуску є частиною
успішної перевірки повтору транзакції та захисту від дубля.

Окремий сценарій штатного випуску 3 із 5 підтвердив: 3 готові вироби на
складі, 6 витрачених компонентів за нормою 2, відкрите замовлення на залишок 2.
Він не використовує підміни складського проведення та не стверджує
перевірку інтеграції з Enterprise-контролем якості.

Перший Community-прогін мав 2 невідповідності очікуваних старих текстів
помилок із 74 тестів. Очікування перекладено, повторний повний прогін
завершився без помилок. Повторний прогін є підсумковим результатом.

Журнали в цьому модулі:

- `docs/evidence/interface_fixes_portable_2026-11-03.txt`
- `docs/evidence/interface_fixes_community_orm_2026-11-03.txt`

Відтворення автономних перевірок з кореня репозиторію:

```powershell
python extra-addons/quality_vataga/tests/run_portable_tests.py
```

Потрібний `lxml`. Для Community ORM використати окремий тестовий PostgreSQL,
задати `PGUSER` / `PGPASSWORD` та виконати:

```powershell
python extra-addons/quality_vataga/tests/run_native_partial_release_tests.py `
  --odoo-source <локальний-каталог-Odoo-17> --port <порт-тестового-PostgreSQL> `
  --run-disposable-tests
```

## Ручна перевірка в Odoo

Виконати на окремій Enterprise-тестовій базі після оновлення лише
`quality_vataga`. Для операційних перевірок потрібна встановлена інтеграція
якості з робочими завданнями. Мова користувачів — українська. Інспектор має
звичайну роль контролю якості та звичайний потрібний доступ до виробництва,
без адміністратора налаштувань. Працівник має звичайну роль виробництва.

1. Відкрити міжопераційну перевірку. Заповнити обов’язкові засоби вимірювання,
   візуальний контроль і значення в допусках, потім встановити одне значення
   поза допуском. Перевірити результати «Придатний» / «Непридатний».
2. Завершити контроль як непридатний. У шапці має бути рівно одна видима
   кнопка «Створити сповіщення». Перевірити також незавершену матрицю з
   реальною невідповідністю: ця кнопка доступна й до остаточного рішення.
3. Під інспектором натиснути кнопку, додати назву й фото, зберегти. Форма
   відкривається без доступу до службових дій. У сповіщенні є лише актуальні
   невідповідності з фактичними значеннями, нормами та причинами.
4. Повернутися до перевірки й повторити відкриття: номер сповіщення той самий.
   Відкрити форму одночасно у двох вкладках до першого збереження. Зберегти
   першу; друга не повинна створити дубль. Оновлення перевірки відкриває
   вже створений запис. Окремо перевірити скасування нової форми без збереження.
5. Відкрити звичайну вхідну перевірку з результатами придатний і непридатний.
   Стандартна кнопка та дія збережені; власна кнопка матриці прихована.
6. Створити виробниче замовлення на 5 штук із двома послідовними операціями,
   міжопераційною точкою контролю та нормою 2 компоненти на виріб. На точці
   увімкнути обов’язковий поштучний маршрут. До створення перевірки встановити
   фактичну кількість виробництва 3.
7. Відкрити матрицю й дочекатися штатної ідентифікації. Має бути рівно
   3 зразки, 3 ідентифікатори виробів і запуск операції на 3 вироби.
   Оновити форму двічі: кількість та ідентифікатори незмінні.
8. Заповнити всі обов’язкові перевірки й підтвердити придатність. Працівник
   починає роботу своєю штатною кнопкою та натискає завершення. Очікування:
   оброблено 3, залишилося 2, завдання відкрите, його таймер призупинено,
   у поштучній історії 3 записи. Повторний натиск не додає записів.
9. Почати наступну операцію для цих самих трьох виробів і підтвердити їхню
   обробку. Перевірити незмінність плану 5 та нульовий складський випуск
   від цих натискань. Два невиготовлені вироби не мають ідентифікаторів,
   складських рухів або фактичної витрати компонентів.
10. Повторити кроки 6–9 на новому замовленні без обов’язкового поштучного
    режиму, але з міжопераційною ідентифікованою партією. Факт обробки трьох
    також зберігається без вимоги створити п’ять ідентифікаторів.
11. На нових контрольних замовленнях залишити один зразок незаповненим;
    потім окремо створити один непридатний зразок. Неперевірений зразок
    не можна підтвердити як придатний, а недопущений виріб не переходить
    на наступну операцію. Для змішаного завершеного контролю можуть
    продовжуватися лише фактично допущені вироби.
12. Створити відкрите утримуюче сповіщення для непридатного виробу,
    виправити вимірювання й завершити повторний контроль. Поки сповіщення
    відкрите, саме цей виріб не допускається далі; інші придатні вироби
    не повинні отримати його утримання.
13. Для обов’язкового маршруту спробувати «Виготовити все» на 3 із 5.
    Неперевірений частковий випуск має бути відхилений без рухів продукції
    чи компонентів. Не змінювати перевірки, план або ідентифікатори для обходу.
    Замовлення та залишок залишаються відкритими. Часткове підтвердження
    обробки саме по собі не надає права складського випуску.
14. На окремому звичайному замовленні без обов’язкового поштучного маршруту
    і без контролю з утриманнями виготовити 3 із 5 штатним MRP-механізмом.
    У майстрі вибрати створення замовлення на залишок. Перевірити склад:
    готової продукції 3, витрата компонентів 6, замовлення на залишок 2,
    залишок не виготовлений. Подальше продовження або скасування виконувати
    штатними діями відповідного замовлення, без штучних складських фактів.
15. Переглянути повідомлення помилок, підказки, майстри, результати матриці
    та нові записи історії. Тексти українські; реальні номери документів
    з технічними префіксами збережені.

Ці ручні кроки описують приймання; їх не було виконано в доступному середовищі.

## Оновлення тестового середовища

Коміт підготовлено локально; відправлення на GitHub і серверне розгортання
не виконувалися. Для застосування потрібні перезапуск Odoo та явне
оновлення `quality_vataga` (`-u quality_vataga`) у тестовій базі, щоб створити
поле й унікальне обмеження та оновити спадкування форм. Оновити ресурси
браузера. Прямих SQL-виправлень даних немає.

Версію `17.0.2.62` залишено згідно з наявним контрактом репозиторію: підвищення
версії прототипу часткового складського випуску очікує Enterprise-приймання.
Цей коміт не активує прототип і не додає користувацької кнопки такого випуску.

Після Enterprise ORM та ручного приймання рішення щодо розгортання слід
приймати за їхніми фактичними результатами, а не за Community-прогоном.

## Файли коміту

Повний список нижче сформований із Git; існуюча незакомічена зміна
`docs/partial_stock_orm_experiment_2026-10-30.md` до цього коміту не входить.

```text
extra-addons/quality_vataga/docs/evidence/interface_fixes_community_orm_2026-11-03.txt
extra-addons/quality_vataga/docs/evidence/interface_fixes_portable_2026-11-03.txt
extra-addons/quality_vataga/docs/interface_partial_localization_2026-11-03.md
extra-addons/quality_vataga/models/native_backorder.py
extra-addons/quality_vataga/models/native_cost_basis.py
extra-addons/quality_vataga/models/native_employee_cost_audit.py
extra-addons/quality_vataga/models/native_enterprise_integration.py
extra-addons/quality_vataga/models/native_partial_release.py
extra-addons/quality_vataga/models/native_production_loss.py
extra-addons/quality_vataga/models/quality_alert.py
extra-addons/quality_vataga/models/quality_alert_repair.py
extra-addons/quality_vataga/models/quality_alert_scrap.py
extra-addons/quality_vataga/models/quality_check.py
extra-addons/quality_vataga/models/quality_check_alert_description.py
extra-addons/quality_vataga/models/quality_check_equipment_selection.py
extra-addons/quality_vataga/models/quality_check_identity.py
extra-addons/quality_vataga/models/quality_check_in_process.py
extra-addons/quality_vataga/models/quality_check_measurement_column.py
extra-addons/quality_vataga/models/quality_check_sample.py
extra-addons/quality_vataga/models/quality_check_unit_admission.py
extra-addons/quality_vataga/models/quality_full_release.py
extra-addons/quality_vataga/models/quality_matrix_hold.py
extra-addons/quality_vataga/models/quality_native_backorder.py
extra-addons/quality_vataga/models/quality_native_workorder.py
extra-addons/quality_vataga/models/quality_partial_release.py
extra-addons/quality_vataga/models/quality_partial_release_probe.py
extra-addons/quality_vataga/models/quality_production_identity.py
extra-addons/quality_vataga/models/quality_production_loss.py
extra-addons/quality_vataga/models/quality_production_loss_probe.py
extra-addons/quality_vataga/models/quality_stock_continuation.py
extra-addons/quality_vataga/models/quality_stock_posting.py
extra-addons/quality_vataga/models/quality_transfer_mrp.py
extra-addons/quality_vataga/models/quality_unit_admission.py
extra-addons/quality_vataga/models/quality_unit_execution.py
extra-addons/quality_vataga/models/quality_unit_release.py
extra-addons/quality_vataga/models/quality_unit_route_policy.py
extra-addons/quality_vataga/models/quality_unit_stock_release.py
extra-addons/quality_vataga/models/quality_unit_transfer.py
extra-addons/quality_vataga/models/stock_posting_core.py
extra-addons/quality_vataga/security/quality_native_backorder.xml
extra-addons/quality_vataga/security/quality_native_partial_release.xml
extra-addons/quality_vataga/security/quality_unit_execution.xml
extra-addons/quality_vataga/static/src/components/measurement_matrix/measurement_matrix.js
extra-addons/quality_vataga/static/src/components/measurement_matrix/measurement_matrix.xml
extra-addons/quality_vataga/tests/__init__.py
extra-addons/quality_vataga/tests/run_portable_tests.py
extra-addons/quality_vataga/tests/test_native_cost_basis.py
extra-addons/quality_vataga/tests/test_quality_alert_repair.py
extra-addons/quality_vataga/tests/test_quality_archive_contract.py
extra-addons/quality_vataga/tests/test_quality_automatic_workorder_contract.py
extra-addons/quality_vataga/tests/test_quality_check_alert_action.py
extra-addons/quality_vataga/tests/test_quality_defect_contract.py
extra-addons/quality_vataga/tests/test_quality_interface_contract.py
extra-addons/quality_vataga/tests/test_quality_partial_workorder.py
extra-addons/quality_vataga/tests/test_quality_qc_simplification.py
extra-addons/quality_vataga/tests/test_quality_stage14.py
extra-addons/quality_vataga/tests/test_quality_unit_admission.py
extra-addons/quality_vataga/tests/test_stock_release_native_contract.py
extra-addons/quality_vataga/views/quality_alert_repair_views.xml
extra-addons/quality_vataga/views/quality_alert_scrap_views.xml
extra-addons/quality_vataga/views/quality_check_views.xml
extra-addons/quality_vataga/views/quality_native_backorder_views.xml
extra-addons/quality_vataga/views/quality_native_workorder_views.xml
extra-addons/quality_vataga/views/quality_production_loss_views.xml
extra-addons/quality_vataga/views/quality_unit_execution_views.xml
extra-addons/quality_vataga/views/quality_unit_release_views.xml
extra-addons/quality_vataga/views/quality_unit_route_policy_views.xml
extra-addons/quality_vataga/views/quality_unit_stock_release_views.xml
extra-addons/quality_vataga/views/quality_unit_transfer_views.xml
extra-addons/quality_vataga/wizard/quality_alert_repair_views.xml
extra-addons/quality_vataga/wizard/quality_full_release_views.xml
extra-addons/quality_vataga/wizard/quality_full_release_wizard.py
extra-addons/quality_vataga/wizard/quality_matrix_failure_scope.py
extra-addons/quality_vataga/wizard/quality_matrix_failure_scope_views.xml
extra-addons/quality_vataga/wizard/quality_stock_timer_binding.py
extra-addons/quality_vataga/wizard/quality_unit_admission_views.xml
extra-addons/quality_vataga/wizard/quality_unit_execution_views.xml
extra-addons/quality_vataga/wizard/quality_unit_execution_wizard.py
extra-addons/quality_vataga/wizard/quality_unit_release_wizard.py
extra-addons/quality_vataga/wizard/quality_unit_stock_release_wizard.py
extra-addons/quality_vataga/wizard/quality_unit_transfer_views.xml
extra-addons/quality_vataga/wizard/quality_unit_transfer_wizard.py
```
