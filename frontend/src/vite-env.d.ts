/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Почта демонстрационной учётной записи, подставляемая ссылкой под формой входа.
   *
   * Пусто по умолчанию, и ссылка тогда не отображается вовсе. Значение попадает **в сборку**,
   * то есть видно каждому, кто откроет страницу, — заполнять его можно только там же, где
   * включён `SEED_DEMO_USER`, то есть на демонстрационном стенде.
   */
  readonly VITE_DEMO_EMAIL?: string;
  /** Пароль демонстрационной учётной записи. Те же оговорки, что и у `VITE_DEMO_EMAIL`. */
  readonly VITE_DEMO_PASSWORD?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
