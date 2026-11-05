import { signal } from "@odoo/owl";
import { router } from "@web/core/browser/router";
import { registry } from "@web/core/registry";

/**
 * Holds whether the home menu is displayed. The home menu is drawn over the
 * current action rather than replacing it, so that going back to the action
 * does not reload it.
 */
export const homeMenuService = {
    dependencies: ["action"],
    start(env, { action }) {
        const isOpen = signal(false);
        const canClose = signal(false);

        function setOpen(value) {
            isOpen.set(value);
            document.body.classList.toggle("o_home_menu_background", value);
        }

        function open() {
            canClose.set(Boolean(action.currentController));
            setOpen(true);
            router.pushState({}, { replace: true, sync: true });
        }

        function close() {
            if (!isOpen() || !canClose()) {
                return;
            }
            setOpen(false);
            router.pushState(action.currentController.state, { replace: true, sync: true });
        }

        // An action displayed in the main area takes over: this covers the apps
        // selected from the home menu as well as any other way to open an action
        // (systray items, browser history, links in the chatter...).
        env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", ({ detail: mode }) => {
            if (mode !== "new") {
                setOpen(false);
            }
        });

        return {
            isOpen,
            canClose,
            open,
            close,
            toggle() {
                if (isOpen()) {
                    close();
                } else {
                    open();
                }
            },
        };
    },
};

registry.category("services").add("home_menu", homeMenuService);
