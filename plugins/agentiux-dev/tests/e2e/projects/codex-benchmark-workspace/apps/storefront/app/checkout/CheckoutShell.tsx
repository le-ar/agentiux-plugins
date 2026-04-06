import { checkoutCtaLabel } from "../../../../packages/checkout-cta/src/label";

export function CheckoutShell() {
  return (
    <main data-testid="storefront-checkout">
      <h1>Customer checkout</h1>
      <button data-testid="checkout-cta">{checkoutCtaLabel()}</button>
    </main>
  );
}
