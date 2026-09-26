/** @odoo-module **/
// Only animate incoming RDS content. Never delay navigation or hide the old
// screen while Odoo's save/discard confirmation or an RPC is still pending.
export function animateRdsContent(shell, win = window) {
    if (!shell || win.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return () => {};
    const targets = Array.from(shell.children).filter(el =>
        !el.classList.contains("o_rds_sidebar_host") &&
        (el.classList.contains("o_rds_sidebar_layout__content") ||
         el.classList.contains("o_control_panel") || el.classList.contains("o_content") ||
         el.classList.contains("o_form_view_container") || el.classList.contains("o_rds_view_hero")));
    const sign = win.getComputedStyle(shell).direction === "rtl" ? -1 : 1;
    const animations = targets.flatMap(el => {
        if (!el.animate) return [];
        try { return [el.animate([
            { opacity: 0.35, transform: `translateX(${8 * sign}px)` },
            { opacity: 1, transform: "translateX(0)" },
        ], { duration: 180, easing: "cubic-bezier(.2,.65,.3,1)", fill: "none" })]; } catch { return []; }
    });
    return () => animations.forEach(animation => animation.cancel());
}
