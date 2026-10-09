# frontend

Vue 3 + TypeScript + Vite. Слои те же, что на бэкенде, и правило зависимостей то же.

```
src/
├── app/        композиционный корень: main.ts, router, App.vue, AppShell.vue, styles.css
├── shared/     общее: HTTP-клиент, ошибки, переиспользуемые компоненты
└── modules/
    ├── auth/
    │   ├── domain/           типы и правила (Account, Role, roleLabel) — без fetch и без Vue
    │   ├── application/      store сессии и порт AuthGateway
    │   ├── infrastructure/   HttpAuthGateway — единственное место с именами полей API
    │   └── presentation/     LoginView.vue, RegisterView.vue
    └── account/
        └── presentation/     AccountView.vue
```

Почему так разложено:

* **`domain` ничего не знает о сервере.** Поэтому переименование поля в API правится в одном
  файле — `infrastructure/httpAuthGateway.ts`, где `snake_case` превращается в `camelCase`.
* **Компоненты не делают запросов.** Они читают store и вызывают его действия; `fetch` живёт
  только в `shared/api/client.ts`. Иначе логика сессии расползается по экранам.
* **Шлюз подменяем.** `useSessionStore().useGateway(fake)` даёт тест store без сервера.

## Сессия

Сессия лежит в `HttpOnly`-cookie, которую SPA прочитать не может, — единственный, кто знает,
жива ли она, это сервер. Поэтому `restore()` спрашивает `GET /auth/me/` один раз за загрузку
страницы, а не читает `localStorage`: сохранённый профиль означал бы отрисованную шапку для
сессии, истёкшей час назад.

Любой ответ «сессии нет» от любого запроса сбрасывает store через `onSessionLapsed`, и гард
роутера уводит на `/login` — обработка в одном месте, а не на каждом экране.

## Разработка

```bash
npm install
npm run dev        # http://localhost:5173
```

Vite проксирует `/api` на `http://127.0.0.1:8000`, поэтому в браузере SPA и API находятся на
одном origin — cookie ведёт себя ровно так же, как за nginx в продакшене, и нет CORS-пути,
существующего только локально. Адрес бэкенда переопределяется `VITE_API_PROXY`.

```bash
npm run build      # vue-tsc (полная проверка типов) + vite build
```

## Стили

Один файл — `src/app/styles.css`. Токены объявлены на `:root`, все цвета ссылаются на них,
так что ребрендинг — это один блок в начале файла. Дизайн перенесён из проекта TenderHack:
синяя брендовая панель, белая карточка формы, красное основное действие, прямые углы, Open Sans.
