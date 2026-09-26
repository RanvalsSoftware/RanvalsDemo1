/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillUnmount,
    onWillUpdateProps,
    useRef,
    useState,
} from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

const ICON_ROOT = "/ranvals_document_studio/static/src/img/icons";
const MOBILE_QUERY = "(max-width: 767.98px)";
const ROOT_MENU_XMLID = "ranvals_document_studio.menu_rds_root";
let sidebarSequence = 0;
let bodyScopeReferences = 0;

/**
 * This is presentation metadata, not a navigation/action registry.  The actual
 * records are always read from the current user's visible children of
 * ``menu_rds_root``.  Consequently an optional connector entry is absent when
 * its menu is absent or inaccessible, and labels follow translated menu names.
 */
export const RDS_SIDEBAR_ITEMS = Object.freeze([
    Object.freeze({
        key: "connectors",
        menuXmlid: "ranvals_document_studio.menu_rds_connectors",
        icon: "02_cube_total.svg",
    }),
    Object.freeze({
        key: "templates",
        menuXmlid: "ranvals_document_studio.menu_rds_templates",
        icon: "13_pdf_file.svg",
    }),
    Object.freeze({
        key: "invoices",
        menuXmlid: "ranvals_document_studio.menu_rds_invoices",
        icon: "05_accounting_document_calculator.svg",
    }),
    Object.freeze({
        key: "history",
        menuXmlid: "ranvals_document_studio.menu_rds_logs",
        icon: "19_activity_clock.svg",
    }),
]);

/**
 * Ready-made optional entries.  Their destination deliberately remains empty:
 * a host can add the entries and resolve them in ``onNavigate`` without the
 * core module depending on Settings or a documentation application.
 */
export const RDS_SIDEBAR_OPTIONAL_ITEMS = Object.freeze({
    overview: Object.freeze({
        key: "overview",
        label: _t("Genel Bakış"),
        icon: "01_app_document.svg",
    }),
    settings: Object.freeze({
        key: "settings",
        label: _t("Ayarlar"),
        icon: "20_tools_settings.svg",
    }),
    help: Object.freeze({
        key: "help",
        label: _t("Yardım"),
        icon: "12_info_circle.svg",
    }),
});

/**
 * Explicit action aliases are the scoping contract for consumers.  There is no
 * global WebClient/Layout patch and no broad res_model matching.  A custom
 * renderer can call ``getRdsSidebarActiveKey(action)``; if it returns null the
 * sidebar must not be mounted for that action.
 */
export const RDS_SIDEBAR_SCOPES = Object.freeze([
    Object.freeze({
        key: "connectors",
        identities: Object.freeze([
            "ranvals_document_studio.action_rds_connectors_dashboard",
            "ranvals_document_studio.menu_rds_connectors",
            "ranvals_document_studio.connectors_dashboard",
            "rds-connectors",
        ]),
    }),
    Object.freeze({
        key: "templates",
        identities: Object.freeze([
            "ranvals_document_studio.action_rds_template",
            "ranvals_document_studio.menu_rds_templates",
        ]),
    }),
    Object.freeze({
        key: "invoices",
        identities: Object.freeze([
            "ranvals_document_studio.action_rds_invoice",
            "ranvals_document_studio.menu_rds_invoices",
        ]),
    }),
    Object.freeze({
        key: "history",
        identities: Object.freeze([
            "ranvals_document_studio.action_rds_export_dashboard",
            "ranvals_document_studio.menu_rds_logs",
            "ranvals_document_studio.export_dashboard",
            "rds-export-history",
        ]),
    }),
]);

function actionIdentityTokens(action) {
    if (!action) {
        return new Set();
    }
    if (typeof action === "string") {
        return new Set([action]);
    }
    const tokens = [
        action.xml_id,
        action.xmlId,
        action.actionXmlId,
        action.tag,
        action.path,
        action.params?.xml_id,
        action.params?.xmlId,
        action.params?.action,
    ];
    return new Set(tokens.filter((token) => typeof token === "string" && token));
}

function menuXmlid(menu) {
    return menu?.xmlid || menu?.xmlId || menu?.xml_id || "";
}

function acquireBodyScope() {
    bodyScopeReferences += 1;
    if (bodyScopeReferences === 1) {
        document.body.classList.add("o_rds_sidebar_active");
    }
}

function releaseBodyScope() {
    bodyScopeReferences = Math.max(0, bodyScopeReferences - 1);
    if (bodyScopeReferences === 0) {
        document.body.classList.remove("o_rds_sidebar_active");
    }
}

export function getRdsSidebarActiveKey(action) {
    const tokens = actionIdentityTokens(action);
    for (const scope of RDS_SIDEBAR_SCOPES) {
        if (scope.identities.some((identity) => tokens.has(identity))) {
            return scope.key;
        }
    }
    return null;
}

export function isRdsSidebarAction(action) {
    return Boolean(getRdsSidebarActiveKey(action));
}

/**
 * Convenience helper for hosts that receive an Odoo action object.  Returning
 * null makes the intended mounting rule explicit and avoids leaking the shell
 * into unrelated list/form views.
 */
export function getRdsSidebarProps(action, overrides = {}) {
    const activeKey = getRdsSidebarActiveKey(action);
    return activeKey ? { activeKey, scope: activeKey, ...overrides } : null;
}

export class RdsSidebar extends Component {
    static template = "ranvals_document_studio.RdsSidebar";
    static props = {
        activeKey: { type: String, optional: true },
        activeMenuXmlid: { type: String, optional: true },
        scope: { type: String, optional: true },
        items: { type: Array, optional: true },
        auxiliaryItems: { type: Array, optional: true },
        enabled: { type: Boolean, optional: true },
        drawerOpen: { type: Boolean, optional: true },
        collapsed: { type: Boolean, optional: true },
        collapsible: { type: Boolean, optional: true },
        showMobileToggle: { type: Boolean, optional: true },
        closeOnNavigate: { type: Boolean, optional: true },
        homeKey: { type: String, optional: true },
        ariaLabel: { type: String, optional: true },
        onNavigate: { type: Function, optional: true },
        onDrawerChange: { type: Function, optional: true },
        onCollapsedChange: { type: Function, optional: true },
        slots: { type: Object, optional: true },
    };
    static defaultProps = {
        items: RDS_SIDEBAR_ITEMS,
        auxiliaryItems: [],
        enabled: true,
        collapsible: false,
        showMobileToggle: true,
        closeOnNavigate: true,
        homeKey: "connectors",
        ariaLabel: _t("DocuCraft bölümleri"),
    };

    setup() {
        this.actionService = useService("action");
        this.menuService = useService("menu");
        this.panelRef = useRef("panel");
        this.openerRef = useRef("opener");
        this.state = useState({
            drawerOpen: false,
            collapsed: false,
            isMobile: false,
        });
        this.sidebarId = `rds-sidebar-${++sidebarSequence}`;
        this.hasBodyScope = false;

        onMounted(() => {
            this.setBodyScope(this.props.enabled);
            this.mediaQuery = window.matchMedia(MOBILE_QUERY);
            this.state.isMobile = this.mediaQuery.matches;
            this.onMediaChange = (event) => {
                this.state.isMobile = event.matches;
                if (!event.matches) {
                    this.setDrawerOpen(false, { restoreFocus: false });
                }
            };
            this.onDocumentKeydown = (event) => this.handleDocumentKeydown(event);
            this.onMenusChanged = () => this.render();
            this.mediaQuery.addEventListener?.("change", this.onMediaChange);
            document.addEventListener("keydown", this.onDocumentKeydown);
            this.env.bus.addEventListener("MENUS:APP-CHANGED", this.onMenusChanged);
        });

        onWillUpdateProps((nextProps) => this.setBodyScope(nextProps.enabled !== false));

        onWillUnmount(() => {
            this.setBodyScope(false);
            this.mediaQuery?.removeEventListener?.("change", this.onMediaChange);
            if (this.onDocumentKeydown) {
                document.removeEventListener("keydown", this.onDocumentKeydown);
            }
            if (this.onMenusChanged) {
                this.env.bus.removeEventListener("MENUS:APP-CHANGED", this.onMenusChanged);
            }
        });
    }

    get rdsRootMenu() {
        return this.menuService.getApps().find((menu) => menuXmlid(menu) === ROOT_MENU_XMLID);
    }

    get navigationItems() {
        const rootMenu = this.rdsRootMenu;
        if (!rootMenu) {
            return [];
        }
        const metadata = new Map(
            (this.props.items || []).map((item) => [item.menuXmlid, item])
        );
        const visibleChildren = this.menuService.getMenuAsTree(rootMenu.id).childrenTree || [];
        return visibleChildren.flatMap((menu) => {
            const xmlid = menuXmlid(menu);
            const item = metadata.get(xmlid);
            return item ? [{ ...item, label: menu.name, menu, menuXmlid: xmlid }] : [];
        });
    }

    get footerItems() {
        return this.props.auxiliaryItems || [];
    }

    get isDrawerOpen() {
        return this.props.drawerOpen === undefined
            ? this.state.drawerOpen
            : this.props.drawerOpen;
    }

    get isCollapsed() {
        return this.props.collapsed === undefined
            ? this.state.collapsed
            : this.props.collapsed;
    }

    get hostClass() {
        return [
            "o_rds_sidebar_host",
            this.isDrawerOpen ? "is-drawer-open" : "",
            this.isCollapsed ? "is-collapsed" : "",
        ].filter(Boolean).join(" ");
    }

    get resolvedActiveKey() {
        return this.props.activeKey ||
            this.props.scope ||
            getRdsSidebarActiveKey(this.props.activeMenuXmlid);
    }

    setBodyScope(enabled) {
        if (enabled && !this.hasBodyScope) {
            acquireBodyScope();
            this.hasBodyScope = true;
        } else if (!enabled && this.hasBodyScope) {
            releaseBodyScope();
            this.hasBodyScope = false;
        }
    }

    icon(fileName) {
        return `${ICON_ROOT}/${fileName}`;
    }

    isActive(item) {
        return item.key === this.resolvedActiveKey;
    }

    navClass(item) {
        return [
            "o_rds_sidebar__item",
            this.isActive(item) ? "is-active" : "",
            item.disabled ? "is-disabled" : "",
        ].filter(Boolean).join(" ");
    }

    async activate(item) {
        if (!item || item.disabled) {
            return;
        }
        if (this.props.closeOnNavigate) {
            this.setDrawerOpen(false, { restoreFocus: false });
        }

        let useDefaultNavigation = true;
        if (this.props.onNavigate) {
            useDefaultNavigation = (await this.props.onNavigate(item)) !== false;
        }
        if (!useDefaultNavigation) {
            return;
        }
        if (item.menu) {
            return this.menuService.selectMenu(item.menu);
        }
        if (item.action) {
            return this.actionService.doAction(item.action, item.actionOptions || {});
        }
        if (item.href) {
            if (item.target === "_blank") {
                window.open(item.href, "_blank", "noopener,noreferrer");
            } else {
                window.location.assign(item.href);
            }
        }
    }

    openHome() {
        const homeItem = this.navigationItems.find((item) => item.key === this.props.homeKey);
        if (homeItem) {
            return this.activate(homeItem);
        }
    }

    toggleDrawer() {
        this.setDrawerOpen(!this.isDrawerOpen, { restoreFocus: this.isDrawerOpen });
    }

    closeDrawer() {
        this.setDrawerOpen(false, { restoreFocus: true });
    }

    setDrawerOpen(open, { restoreFocus = false } = {}) {
        if (this.props.drawerOpen === undefined) {
            this.state.drawerOpen = open;
        }
        this.props.onDrawerChange?.(open);

        if (open) {
            window.requestAnimationFrame(() => {
                this.panelRef.el?.querySelector("button:not([disabled]), a[href]")?.focus();
            });
        } else if (restoreFocus) {
            window.requestAnimationFrame(() => this.openerRef.el?.focus());
        }
    }

    toggleCollapsed() {
        const collapsed = !this.isCollapsed;
        if (this.props.collapsed === undefined) {
            this.state.collapsed = collapsed;
        }
        this.props.onCollapsedChange?.(collapsed);
    }

    handleDocumentKeydown(event) {
        if (!this.state.isMobile || !this.isDrawerOpen) {
            return;
        }
        if (event.key === "Escape") {
            event.preventDefault();
            this.closeDrawer();
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        const focusable = Array.from(
            this.panelRef.el?.querySelectorAll(
                'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
            ) || []
        );
        if (!focusable.length) {
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
}
