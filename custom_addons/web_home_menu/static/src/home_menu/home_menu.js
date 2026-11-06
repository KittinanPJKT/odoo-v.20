import { Component, computed, signal, useEffect, useListener, usePlugin } from "@odoo/owl";
import { OfflinePlugin } from "@web/core/offline/offline_plugin";
import { registry } from "@web/core/registry";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { fuzzyLookup } from "@web/core/utils/search";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

/**
 * The server sends raw base64 icons, while the default icon is already a URL.
 *
 * @param {string} webIconData
 * @returns {string}
 */
function getIconSrc(webIconData) {
    if (webIconData.startsWith("data:image") || webIconData.startsWith("/")) {
        return webIconData;
    }
    const prefix = webIconData.startsWith("P")
        ? "data:image/svg+xml;base64,"
        : "data:image/png;base64,";
    return prefix + webIconData.replace(/\s/g, "");
}

export class HomeMenu extends Component {
    static template = "web_home_menu.HomeMenu";

    root = signal.ref();
    grid = signal.ref();
    input = signal.ref();
    query = signal("");
    /** Index of the keyboard selection in the apps followed by the menu items. */
    focusedIndex = signal(null);

    menus = computed(() => {
        const { apps, menuItems } = computeAppsAndMenuItems(
            this.menuService.getMenuAsTree("root")
        );
        for (const app of apps) {
            app.iconSrc = app.webIconData && getIconSrc(app.webIconData);
        }
        return { apps, menuItems };
    });

    results = computed(() => {
        const { apps, menuItems } = this.menus();
        const query = this.query().trim();
        if (!query) {
            return { apps, menuItems: [] };
        }
        return {
            apps: fuzzyLookup(query, apps, (app) => app.label),
            menuItems: fuzzyLookup(query, menuItems, (item) => {
                const breadcrumb = `${item.parents} / ${item.label}`;
                return [breadcrumb, breadcrumb.split("/").reverse().join("/")];
            }),
        };
    });

    setup() {
        this.homeMenu = useService("home_menu");
        this.menuService = useService("menu");
        this.ui = useService("ui");
        this.offlinePlugin = usePlugin(OfflinePlugin);
        useAutofocus({ ref: this.input });
        useListener(window, "keydown", this.onWindowKeydown.bind(this), { capture: true });
        useEffect(() => {
            if (this.focusedIndex() !== null) {
                this.root()
                    ?.querySelector(".o_home_menu_item_focused")
                    ?.scrollIntoView({ block: "nearest" });
            }
        });
    }

    getAllResults() {
        const { apps, menuItems } = this.results();
        return [...apps, ...menuItems];
    }

    isDisabledOffline(item) {
        return (
            this.offlinePlugin.isOffline() && !this.offlinePlugin.isAvailableOffline(item.actionID)
        );
    }

    selectItem(item) {
        this.menuService.selectMenu(item);
    }

    getGridColumnCount() {
        const columns = getComputedStyle(this.grid()).gridTemplateColumns;
        return columns.split(" ").length;
    }

    /**
     * @param {string} key one of the arrow keys
     * @returns {number} the next index of the keyboard selection
     */
    getNextIndex(key) {
        const appCount = this.results().apps.length;
        const count = this.getAllResults().length;
        const index = this.focusedIndex();
        if (index === null) {
            return 0;
        }
        if (key === "ArrowRight") {
            return (index + 1) % count;
        }
        if (key === "ArrowLeft") {
            return (index - 1 + count) % count;
        }
        const isInGrid = index < appCount;
        const columns = isInGrid ? this.getGridColumnCount() : 1;
        if (key === "ArrowDown") {
            if (!isInGrid) {
                return (index + 1) % count;
            }
            if (index + columns < appCount) {
                return index + columns;
            }
            return count > appCount ? appCount : index % columns;
        }
        if (!isInGrid) {
            return index - 1;
        }
        if (index - columns >= 0) {
            return index - columns;
        }
        if (count > appCount) {
            return count - 1;
        }
        const lastRowIndex = Math.floor((appCount - 1) / columns) * columns + index;
        return lastRowIndex < appCount ? lastRowIndex : lastRowIndex - columns;
    }

    onInput(ev) {
        this.query.set(ev.target.value);
        this.focusedIndex.set(this.query().trim() && this.getAllResults().length ? 0 : null);
    }

    /**
     * Listens on the window as the search input may have lost the focus, for
     * instance after a click on the background. It does so during the capture
     * phase as the action hidden below the home menu is still mounted, and must
     * not react to the keys handled here.
     *
     * @param {KeyboardEvent} ev
     */
    onWindowKeydown(ev) {
        const activeElement = document.activeElement;
        if (
            ev.isComposing ||
            ev.ctrlKey ||
            ev.metaKey ||
            ev.altKey ||
            this.ui.activeElement !== document ||
            // Leave the keyboard to whatever else has the focus: an open dropdown
            // of the navbar, a chat window...
            (activeElement !== document.body && !this.root()?.contains(activeElement))
        ) {
            return;
        }
        const results = this.getAllResults();
        const focusedItem = results[this.focusedIndex()];
        switch (ev.key) {
            case "ArrowUp":
            case "ArrowDown":
            case "ArrowLeft":
            case "ArrowRight":
                if (results.length) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    this.focusedIndex.set(this.getNextIndex(ev.key));
                }
                return;
            case "Enter":
                if (focusedItem) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    this.selectItem(focusedItem);
                }
                return;
            case "Escape":
                ev.preventDefault();
                ev.stopPropagation();
                if (this.query()) {
                    this.query.set("");
                    this.focusedIndex.set(null);
                } else {
                    this.homeMenu.close();
                }
                return;
        }
        // Printable characters and Backspace are sent to the search input by the
        // browser, as the focus moves before their default action.
        if (ev.key.length === 1 || ev.key === "Backspace") {
            this.input()?.focus();
        }
    }
}

export class HomeMenuContainer extends Component {
    static template = "web_home_menu.HomeMenuContainer";
    static components = { HomeMenu };

    setup() {
        this.homeMenu = useService("home_menu");
    }
}

registry.category("main_components").add("HomeMenu", { Component: HomeMenuContainer });
