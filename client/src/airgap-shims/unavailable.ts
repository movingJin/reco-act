// airgap(폐쇄망) 웹 빌드의 네이티브 플러그인 대역(01-plan 4.1).
// 컴포넌트는 Capacitor.isNativePlatform()이 true일 때만 플러그인을 부르므로, 웹에서는 실제로 불리지 않는다.
// 혹시 불리면 조용히 넘어가지 않도록 예외를 낸다.
export function unavailable<T>(name: string): T {
  return new Proxy({} as Record<string | symbol, unknown>, {
    get(_target, property) {
      // await나 Promise.resolve가 thenable로 오인하지 않게 한다.
      if (property === 'then' || typeof property === 'symbol') return undefined;
      return () => {
        throw new Error(`${name}.${String(property)} is not available in the airgap web build`);
      };
    },
  }) as T;
}
