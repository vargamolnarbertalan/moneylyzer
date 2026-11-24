/** The product mark and wordmark. The logo is decorative next to the name, so its alt text is empty. */
export function Brand({ size = 30 }: { size?: number }) {
  return (
    <div className="brand">
      <img src="/logo.svg" alt="" width={size} height={size} />
      <span>Moneylyzer</span>
    </div>
  );
}
