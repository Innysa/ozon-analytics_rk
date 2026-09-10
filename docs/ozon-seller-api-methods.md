# Список методов Ozon Seller API, доступных в ключе (справочник)

Собрано пользователем из личного кабинета Ozon (раздел «Доступные роли и
методы» у ключа с ролью Admin — доступ ко всем методам Seller API) по
состоянию на 2026-09-10. Это **список того, что вообще существует и
доступно этому ключу** — не Redoc-схема запроса/ответа. Перед тем как
опираться на какой-либо из методов ниже в коде, контракт (поля, форма
ответа) всё равно нужно подтверждать отдельно: через официальную
документацию (`docs.ozon.ru/api/seller/` — недоступна из sandbox-среды
разработки, но доступна пользователю в браузере) или через реальный
диагностический прогон (см. `backend/scripts/debug_*.py`), как это уже
делалось для каждого метода, используемого в этом проекте.

Держите этот файл под рукой при добавлении новых интеграций — экономит
время на попытки вызвать метод, которого либо никогда не было, либо он уже
устарел (как обнаружилось с `/v3/finance/transaction/list`, см. ниже).

## Уже использует этот проект

- `/v1/analytics/data` — воронка карточки товара (Premium Plus), см. README
- `/v1/analytics/product-queries/details` — позиции в поиске, см. README
- `/v2/posting/fbo/list`, `/v3/posting/fbs/list` — заказы (РНП/Маржа), см. README
- `/v3/product/list`, `/v3/product/info/list` — каталог/остатки/цены
- `/v1/review/list`, `/v1/review/info`, `/v1/review/comment/list`, `/v1/review/comment/create` — отзывы

## Финансы (finance) — актуально на 2026-09-10

- `/v1/finance/balance` — баланс
- `/v1/finance/accrual/postings` — начисления по отправлениям
- `/v1/finance/accrual/by-day` — начисления по дням
- `/v1/finance/accrual/types` — типы начислений (справочник)
- `/v1/finance/realization/posting` — реализация по отправлению
- `/v1/finance/realization/by-day` — реализация по дням
- `/v2/finance/realization` — отчёт о реализации (существовал и раньше)
- `/v1/finance/cash-flow-statement/list` — отчёт ДДС (движение денежных
  средств) — **кандидат для Логистики/Хранения/Штрафов/Прочих удержаний на
  Дашборде** (см. README, раздел «Заказы и финансы»); контракт ЧАСТИЧНО
  подтверждён (запрос — `{"date": {"from", "to"}, "page", "page_size"}`,
  ответ — `cash_flows[]` с `orders_amount`/`returns_amount`/
  `commission_amount`/`services_amount`/`item_delivery_and_return_amount`),
  но периодизация и разбивка `services_amount` на логистику/хранение/штрафы
  по отдельности — ещё нет; продолжение разведки —
  `backend/scripts/debug_cash_flow_statement.py`
- `/v1/finance/products/buyout` — выкуп товаров
- `/v1/finance/decompensation` — декомпенсация
- `/v1/finance/compensation` — компенсация
- `/v1/finance/mutual-settlement` — взаиморасчёты
- `/v1/finance/document-b2b-sales`, `/v1/finance/document-b2b-sales/json` — документы B2B продаж

**`/v3/finance/transaction/list` В СПИСКЕ ОТСУТСТВУЕТ** — этот метод
подтверждённо устарел (Ozon отвечает `HTTP 400 {"code": 9, "message":
"obsolete method cannot be used"}`, см. README и
`OzonSellerClient.list_finance_transactions()`'s own docstring). Не
пытайтесь его вызывать снова.

## Аналитика (analytics)

- `/v1/analytics/data` — используется (воронка карточки)
- `/v2/analytics/stock_on_warehouses` — возможный кандидат на более точные
  «Остатки товаров» (по складам, не просто fbo/fbs-сумма из каталога) —
  контракт не проверялся, не используется
- `/v1/analytics/category/comparison`
- `/v1/analytics/product-queries`
- `/v1/analytics/product-queries/details` — используется (позиции в поиске)
- `/v1/analytics/turnover/stocks` — оборачиваемость остатков, не используется
- `/v1/analytics/stocks` — не используется

## Отчёты (report)

`/v1/report/placement/by-products/create`, `/by-supplies/create`,
`/v1/report/postings/create`, `/v1/report/info`, `/v1/report/list`,
`/v1/report/marked-products-sales/create`, `/v1/report/file/*`,
`/v1/report/realization/posting/create`, `/v1/report/discounted/{info,create,list}`,
`/v1/report/warehouse/stock`, `/v1/report/products/create`, `/v2/report/returns/create`

## Отправления FBS (posting/fbs)

`/v4/posting/fbs/list` (актуальнее используемого в проекте `/v3/posting/fbs/list`
— стоит проверить при следующем пересмотре заказов), `/v4/posting/fbs/ship`,
`/ship/package`, `/v3/posting/fbs/get`, `/v3/posting/fbs/list`,
`/unfulfilled/list`, `/v4/posting/fbs/unfulfilled/list`, `/v2/posting/fbs/cancel`,
`/cancel-reason/list`, `/v1/posting/fbs/cancel-reason`, `/v2/posting/fbs/product/cancel`,
`/v1/posting/fbs/split`, `/v1/posting/fbs/{presort,list,validate,box/list}`,
`/v1/posting/fbs/traceable/split`, `/v2/posting/fbs/product/mandatory-mark/{validate,is-required}`,
`/v1/posting/fbs/product/traceable/attribute`, `/v2/posting/fbs/product/country/{set,list}`,
`/v2/posting/fbs/get-by-barcode`, `/v2/posting/fbs/act/*`, `/v3/posting/fbs/act/get-postings`,
`/v1/posting/fbs/package-label/{create,get}`, `/v2/posting/fbs/package-label*`,
`/v1/posting/fbs/pick-up-code/verify`, `/v1/posting/fbs/click-and-collect/ship`,
`/v1/posting/fbs/timeslot/{set,change-restrictions}`, `/v2/posting/fbs/tracking-number/set`,
`/v1/posting/fbs/restrictions`, `/v2/posting/fbs/arbitration`, `/v2/posting/fbs/awaiting-delivery`,
`/v2/fbs/posting/{delivered,delivering,last-mile}`, `/v6/fbs/posting/product/exemplar/*`,
`/v5/fbs/posting/product/exemplar/*`, `/v1/fbs/posting/product/exemplar/update`,
`/beta/posting/fbs/split`

## Отправления FBO (posting/fbo)

`/v2/posting/fbo/list` (используется), `/v3/posting/fbo/list` (новее — не
используется, `/v2/` подтверждён и работает), `/v2/posting/fbo/get`,
`/v1/posting/fbo/cancel-reason/list`

## Отправления FBP (fbp)

`/v1/fbp/order/*`, `/v1/fbp/draft/*`, `/v1/fbp/act-from/*`, `/v1/fbp/act-to/*`,
`/v1/fbp/archive/*`, `/v1/fbp/label/*`, `/v1/fbp/warehouse/list`

## Отправления/прочее (posting общее)

`/v1/posting/cutoff/set`, `/v1/posting/fj/tracking-number/*`, `/v2/posting/digital/list`,
`/v1/posting/digital/codes/*`, `/v1/posting/global/etgb`, `/v1/posting/unpaid-legal/product/list`,
`/v3/posting/multiboxqty/set`, `/v1/posting/fbp/{list,get}`

## Склады (warehouse)

`/v2/warehouse/list`, `/v1/warehouse/ozon/list`, `/v1/warehouse/fbo/*`,
`/v1/warehouse/{unarchive,archive,update,create}`, `/v1/warehouse/operation/status`,
`/v1/warehouse/invalid-products/get`, `/v1/warehouse/warehouses-with-invalid-products`,
`/v1/warehouse/fbs/*` (создание/обновление/таймслоты/пункты выдачи/drop-off),
`/v1/warehouse/rfbs/{pause,unpause}`, `/v1/warehouse/erfbs/*`,
`/v1/delivery-method/return/settings/get`, `/v2/delivery-method/list`,
`/v2/delivery/checkout`, `/v1/returns/settings/utilization/*`

## Грузы/перевозки (cargoes, carriage)

`/v1/cargoes/*`, `/v2/cargoes/*`, `/v1/cargoes/transport/*`, `/v1/cargoes-label/*`,
`/v1/cargoes/label/*`, `/v1/carriage/*`, `/v1/carriage/container/*`,
`/v1/carriage/pass/*`, `/v1/carriage/courier-contact/*`, `/v1/carriage/set-postings`,
`/v2/carriage/delivery/list`, `/v1/pass/list`

## Товары (product)

`/v3/product/{list,import,info/list}` (list/info-list используются),
`/v2/products/{delete,stocks}`, `/v1/products/prices`,
`/v4/product/info/{limit,attributes,stocks}`, `/v5/product/info/prices`,
`/v1/product/info/{description,subscription,warehouse/stocks,discounted,wrong-volume,stocks-by-warehouse/fbo}`,
`/v2/product/info/stocks-by-warehouse/fbs`, `/v1/product/import/*`, `/v1/product/import-by-sku`,
`/v1/product/attributes/update`, `/v1/product/update/{offer-id,discount}`,
`/v1/product/related-sku/get`, `/v1/product/visibility/{set,info}`,
`/v1/product/{archive,unarchive}`, `/v1/product/rating-by-sku`,
`/v1/product/quant/{list,info}`, `/v1/product/action/timer/*`,
`/v1/product/stairway-discount/by-quantity/*`, `/v1/product/placement-zone/info`,
`/v1/product/prices/details`, `/v2/product/pictures/*`, `/v1/product/pictures/import`,
`/v1/product/digital/stocks/import`, `/v1/product/certificate/*`, `/v2/product/certificate/*`,
`/v2/product/certification/*`

## Ценообразование (pricing-strategy)

`/v1/pricing-strategy/{list,create,update,delete,info,status}`,
`/v1/pricing-strategy/strategy-ids-by-product-ids`,
`/v1/pricing-strategy/products/{list,add,delete}`,
`/v1/pricing-strategy/product/info`, `/v1/pricing-strategy/competitors/list`

## Поставки (supply-order, draft)

`/v3/supply-order/{get,list}`, `/v1/supply-order/cancel*`, `/v1/supply-order/content/*`,
`/v1/supply-order/act/*`, `/v1/supply-order/timeslot/*`, `/v2/supply-order/timeslot/list`,
`/v1/supply-order/status/counter`, `/v1/supply-order/shipment-plan-compliance/get`,
`/v1/supply-order/{details,bundle}`, `/v1/supply-order/pass/*`,
`/v1/draft/{direct,crossdock,multi-cluster}/create`, `/v2/draft/*`,
`/v2/cluster/list`, `/v1/cluster/list`

## Вопросы и отзывы (question, review)

`/v3/question/list`, `/v2/question/list`, `/v1/question/*`, `/v1/question/answer/*`,
`/v1/review/*` (используется), `/v2/review/*`, `/v1/review/comment/*`, `/v2/review/comment/delete`

## Рейтинг (rating)

`/v1/rating/index/fbs/*`, `/v1/rating/history`, `/v1/rating/summary`

## Чат (chat)

`/v3/chat/{list,history}`, `/v1/chat/send/*`, `/v1/chat/start`, `/v2/chat/*`

## Возвраты (returns, return)

`/v1/returns/list`, `/v1/returns/company/fbs/info`, `/v2/returns/rfbs/{list,get}`,
`/v1/returns/rfbs/action/set`, `/v1/return/pass/*`, `/v1/return/giveout/*`,
`/v1/removal/{from-supply,from-stock}/list`

## Акции и скидки (actions, seller-actions)

`/v1/actions*`, `/v1/actions/products*`, `/v1/actions/auto-add/products/*`,
`/v1/actions/discounts-task/*`, `/v2/actions/discounts-task/list`,
`/v1/seller-actions/*`

## Условная отмена (conditional-cancellation)

`/v2/conditional-cancellation/{approve,reject,list}`

## Штрихкоды, накладные (barcode, invoice)

`/v1/barcode/{add,generate}`, `/v2/invoice/create-or-update`, `/v2/invoice/get`,
`/v1/invoice/file/{upload,delete}`

## Полигоны доставки (polygon)

`/v1/polygon/*`, `/v2/polygon/bind`

## Сборка (assembly)

`/v1/assembly/carriage/posting/list`, `/product/list`,
`/v1/assembly/fbs/posting/list`, `/product/list`

## Уведомления (notification)

`/v1/notification/*`, `/v1/notification/push-type/list`

## Прочее

`/v2/order/create`, `/v1/search-queries/{text,top}`,
`/v1/description-category/*`, `/v1/roles`, `/v1/brand/company-certification/list`,
`/v1/seller/info`, `/v1/seller/ozon-logistics/info`, `/v1/supplier/available_warehouses`
