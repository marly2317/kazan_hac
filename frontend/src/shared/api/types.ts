/** The error envelope every endpoint answers failures with. */
export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details?: Record<string, string[] | string>;
  };
};
