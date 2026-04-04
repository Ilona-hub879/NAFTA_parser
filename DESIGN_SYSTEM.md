# Vibecoder & Automator — дизайн-система для внешних проектов

Документ описывает визуальный язык сайта, чтобы приложение «CRM / интеграции» могло совпадать с ним по стилю. Источник: `index.html`, `style.css`, расширение Tailwind в `<script>`.

---

## 1. Шрифты

| Роль | Шрифт | Веса (Google Fonts) |
|------|--------|----------------------|
| Заголовки, акценты, кнопки, бренд | **Syne** | 600, 700, 800 |
| Основной текст, поля ввода | **Inter** | 400, 500, 600 |

Подключение:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@600;700;800&display=swap" rel="stylesheet">
```

CSS-переменные (если без Tailwind):

```css
font-family: 'Inter', sans-serif;           /* body */
font-family: 'Syne', sans-serif;            /* headings, logo, buttons */
```

Иерархия (как на лендинге):

- Hero H1: `font-bold`, крупно `~4xl → 7xl` (responsive), `text-white`, `leading-tight`
- Секции H2: `font-syne`, `text-3xl md:text-4xl`, `font-bold`, `text-white`; префикс `//` в `text-emerald`
- H3 карточек: `font-syne`, `text-xl` или `text-2xl`, `font-bold`, `text-white`
- Подзаголовки / body: `text-gray-400`, при необходимости `text-lg md:text-xl`, `leading-relaxed`
- Мелкий текст: `text-sm`, `text-gray-500` / `text-gray-400`
- Микро-лейблы (как в signature card): `text-[10px] md:text-[11px]`, `uppercase`, `tracking-[0.2em]`, `text-emerald`

---

## 2. Цветовая палитра

| Токен | Значение | Назначение |
|-------|----------|------------|
| **anthracite** | `#121212` | Фон body, тёмные подложки |
| **emerald** (акцент) | `#00ff7f` | Ссылки, CTA, границы акцента, логотип, цены |
| **darkGlass** | `rgba(255, 255, 255, 0.03)` | Фон поля select / слабые панели |
| **darkBorder** | `rgba(255, 255, 255, 0.05)` | Разделители, тонкие границы |
| Текст основной | Tailwind `text-gray-200` | `#e5e7eb` |
| Текст вторичный | `text-gray-400` | абзацы |
| Текст третичный | `text-gray-500` / `text-gray-600` | футер, капшены |
| Фон карточек (полупрозрачный) | `bg-anthracite/50` | тарифы без «стекла» |
| Золотая рамка «стекла» | `rgba(250, 204, 21, 0.35)` | см. `.glass-card` ниже |

**Выделение текста (selection):** `background: #00ff7f` (emerald), `color: white`.

---

## 3. Фон страницы

Слои (снизу вверх):

1. **Фиксированное фото** (на сайте: `images/backgraund4.jpg`):  
   `center / cover`, `fixed`, `no-repeat`
2. **Оверлей-градиенты** (затемнение углов):

```css
background:
  radial-gradient(circle at top left, rgba(15, 23, 42, 0.9), transparent 55%),
  radial-gradient(circle at bottom right, rgba(15, 23, 42, 0.7), transparent 55%),
  url("images/backgraund4.jpg") center/cover fixed no-repeat;
```

Для CRM без картинки можно оставить только градиенты + `#121212`:

```css
background:
  radial-gradient(circle at top left, rgba(15, 23, 42, 0.92), transparent 55%),
  radial-gradient(circle at bottom right, rgba(15, 23, 42, 0.75), transparent 55%),
  #121212;
```

**Декоративные «орбы»** (опционально, как на сайте):

- Верхний левый: `~500×500px`, `bg-emerald/10`, `rounded-full`, `blur-[120px]`, `fixed`, `-z-20`
- Нижний правый: `bg-emerald/5`, аналогично

---

## 4. Компоненты «стекло»

### Шапка (`.glass-header`)

```css
background: rgba(18, 18, 18, 0.8);
backdrop-filter: blur(16px);
-webkit-backdrop-filter: blur(16px);
border-bottom: 1px solid rgba(255, 255, 255, 0.05); /* border-darkBorder */
```

Высота строки: `h-16`, контент `max-w-6xl mx-auto px-4`.

### Карточка (`.glass-card`)

```css
background: rgba(2, 6, 23, 0.85);
backdrop-filter: blur(14px);
-webkit-backdrop-filter: blur(14px);
border: 1px solid rgba(250, 204, 21, 0.35);
box-shadow: 0 18px 45px 0 rgba(0, 0, 0, 0.7);
```

Радиусы на сайте: `rounded-2xl` (карточки, калькулятор), `rounded-3xl` (signature card), `rounded-xl` (поля, cookie).

---

## 5. Кнопки

**Основной контурный стиль (hero, калькулятор):**

- `rounded-full` или `rounded-xl`
- `bg-emerald/10`, `border border-emerald`, `text-white` (или `text-emerald` для вторичного)
- `font-syne`, `font-bold`
- Hover: `hover:bg-emerald`, `hover:text-anthracite`, тень `0 0 20px rgba(0,255,127,0.4)` (или аналог)
- `transition-all duration-300`

**Плитка «Принять» (cookie):**

- `bg-emerald`, `text-anthracite`, `rounded-lg`, `hover:bg-emerald/90`

**Вторичная (Отклонить):**

- `border border-gray-500`, `text-gray-300`, `hover:bg-gray-700/60`

---

## 6. Поля ввода

```text
flex-1
bg-anthracite/50
border border-darkBorder
focus:border-emerald
rounded-xl
px-6 py-4
text-white
outline-none
font-inter
transition-colors
```

---

## 7. Сетка и отступы

- Контейнер контента: `max-w-6xl mx-auto px-4`
- Вертикальный ритм секций: `space-y-32` между крупными блоками
- Отступ под фиксированный header: `pt-32` у `main`
- Сетка карточек: `grid md:grid-cols-3 gap-6`
- Карточки галереи: `p-6`, hover `hover:-translate-y-2 transition-transform duration-300`

---

## 8. Логотип в шапке

```text
font-syne font-bold text-xl tracking-wider text-emerald
Vibecoder<span class="text-emerald">.</span>
```

---

## 9. Переключатель языка (как на сайте)

- `select`: `appearance-none`, `bg-darkGlass`, `border border-darkBorder`, `hover:border-emerald`, `rounded-full`, `py-1.5 pl-4 pr-9`, `text-sm uppercase tracking-widest`, `font-syne`, `backdrop-blur-md`
- Опции: фон `#020617` (slate-950), текст белый

---

## 10. Футер

- `border-t border-darkBorder`
- Копирайт: `text-gray-500 text-sm`
- Ссылка: `text-emerald`, `hover:text-white`, `underline-offset-2`, `hover:underline`

---

## 11. Фокус и доступность

- Ссылки/карточки: `focus-visible:ring-2 focus-visible:ring-emerald/60`, `focus-visible:ring-offset-2`, `ring-offset-anthracite`
- Кнопки: без лишней обводки, сохранить контраст на тёмном фоне

---

## 12. Tailwind `theme.extend` (копипаст)

Используется на сайте через CDN:

```js
tailwind.config = {
  theme: {
    extend: {
      colors: {
        anthracite: '#121212',
        emerald: '#00ff7f',
        darkGlass: 'rgba(255, 255, 255, 0.03)',
        darkBorder: 'rgba(255, 255, 255, 0.05)',
      },
      fontFamily: {
        syne: ['Syne', 'sans-serif'],
        inter: ['Inter', 'sans-serif'],
      },
    },
  },
};
```

Классы body: `bg-anthracite text-gray-200 font-inter overflow-x-hidden selection:bg-emerald selection:text-white`.

---

## 13. Ресурсы для переноса

| Файл | Назначение |
|------|------------|
| `images/backgraund4.jpg` | Фоновое изображение (опционально) |
| `images/favikon.jpg` | Иконка favicon на сайте |

---

## 14. Краткий чеклист для CRM-приложения

1. Подключить **Syne** + **Inter** с указанными весами.  
2. **Фон:** `#121212` + два radial-gradient угла; при желании — тот же JPG.  
3. **Акцент:** только `#00ff7f` для кнопок, ссылок, активных состояний.  
4. **Панели:** тёмное стекло `rgba(2,6,23,0.85)` + blur + золотистая обводка `rgba(250,204,21,0.35)`.  
5. **Текст:** белый заголовки, `gray-400` описания.  
6. **Кнопки:** контур emerald + заливка при hover как в п. 5.  
7. **Скругления:** `16px–24px` для карточек (`rounded-2xl` / `rounded-3xl`).  
8. **Сетка:** max-width ~`1152px` (`max-w-6xl`), боковые отступы `16px`.

Этого достаточно, чтобы интерфейс CRM визуально совпадал с лендингом Vibecoder & Automator.
