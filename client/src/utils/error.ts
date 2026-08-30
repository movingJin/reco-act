/**
 * axios 에러 응답에서 사용자에게 보여줄 메시지를 안전하게 추출합니다.
 * FastAPI validation 에러(422)의 detail은 문자열이 아닌
 * [{type, loc, msg, input, ctx}] 형태의 배열일 수 있어, 그대로 렌더링하면
 * "object with keys ..." React 에러(#31)로 전체 화면이 크래시한다.
 * 문자열인 경우에만 사용하고, 그 외에는 fallback 메시지를 사용한다.
 */
export const getErrorMessage = (err: any, fallback: string): string => {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};
