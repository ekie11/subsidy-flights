# Отчёт по начислениям — подтверждённая структура (20 августа 2026)

```xml
<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<root>
  <Stats>
    <FareRequestsCount>100000</FareRequestsCount>
    <BookingJumpsCount>999</BookingJumpsCount>
  </Stats>
  <Record Type="REFUND">
    <Id>53914673</Id>
    <DepCity>AER</DepCity>
    <DestCity>TJM</DestCity>
    <DepDate>2026-07-16T00:20:00</DepDate>
    <Created>2026-07-01T00:43:51</Created>
    <CreatedUTC>2026-06-30T14:43:51</CreatedUTC>
    <PersonID>pmnv7</PersonID>
    <PNR>AAA122</PNR>
    <TicketNumber>5559999888888</TicketNumber>
    <TotalSumma>9883.00</TotalSumma>
    <PartnerID>KirillTest</PartnerID>
    <DirectEntrance>0</DirectEntrance>
    <PaymentType>ПК</PaymentType>
    <OperatingCarrier>EO</OperatingCarrier>
  </Record>
  <Record Type="SALE">
    <Id>53914675</Id>
    <DepCity>SVX</DepCity>
    <DestCity>IJK</DestCity>
    <DepDate>2026-07-03T21:30:00</DepDate>
    <Created>2026-07-01T00:44:20</Created>
    <CreatedUTC>2026-06-30T14:44:20</CreatedUTC>
    <PersonID>pmnv8</PersonID>
    <PNR>AAA123</PNR>
    <TicketNumber>5559999888888</TicketNumber>
    <TotalSumma>5410.00</TotalSumma>
    <PartnerID>KirillTest</PartnerID>
    <DirectEntrance>0</DirectEntrance>
    <PaymentType>ПК</PaymentType>
    <OperatingCarrier>I8</OperatingCarrier>
  </Record>
</root>
```

| Поле | Значение |
|---|---|
| `Stats/FareRequestsCount` | Число запросов к API за период (числитель look-to-book) |
| `Stats/BookingJumpsCount` | Число переходов на покупку (знаменатель look-to-book). Держать ≤ 1000:1 |
| `Record Type` | `SALE` — продажа, `REFUND` — возврат. При подсчёте выручки учитывать тип |
| `Id` | Внутренний ID записи у партнёра |
| `DepCity` / `DestCity` | Города вылета/назначения |
| `DepDate` | Дата и время вылета |
| `Created` / `CreatedUTC` | Дата продажи по Хабаровску / по UTC (разница 10 ч) |
| `PersonID` | Наш собственный ID, который мы вставляем в `BookURL` — для сверки переходов с продажами. Нужно генерировать самим |
| `PNR` | Номер брони (совпадение `AAA…` с кодом категории — случайное) |
| `TicketNumber` | Номер билета |
| `TotalSumma` | Сумма операции |
| `PartnerID` | Код партнёра (`KirillTest`, пока нет боевого) |
| `DirectEntrance` | `1` — переход по cookie, `0` — прямой по ссылке |
| `PaymentType` | Форма оплаты — не важна |
| `OperatingCarrier` | Код перевозчика |

Выводы: построить генерацию `PersonID` и вставку в `BookURL`; сверять
look-to-book по `Stats`; не суммировать `TotalSumma` без учёта `Record Type`.
