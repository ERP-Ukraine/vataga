/** @odoo-module **/

export function stockMinimumClass(onHand, minimum) {
    if (!Number.isFinite(onHand) || !Number.isFinite(minimum)) {
        return "";
    }
    // Quantities have already been rounded by Odoo to the product UoM.
    // Absorb only binary floating-point noise, not fractional stock quantities.
    const tolerance = Number.EPSILON * Math.max(Math.abs(onHand), minimum) * 4;
    if (onHand < minimum - tolerance) {
        return "o_stock_minimum_control_red";
    }
    if (onHand <= minimum * 1.1 + tolerance) {
        return "o_stock_minimum_control_yellow";
    }
    return "o_stock_minimum_control_green";
}
