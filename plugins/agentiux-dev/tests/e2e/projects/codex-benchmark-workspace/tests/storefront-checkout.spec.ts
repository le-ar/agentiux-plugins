import { expect, test } from "@playwright/test";

test("@storefront checkout CTA renders the shared label", async ({ page }) => {
  await page.goto("/checkout");

  await expect(page.getByTestId("checkout-cta")).toHaveText("Continue");
});
