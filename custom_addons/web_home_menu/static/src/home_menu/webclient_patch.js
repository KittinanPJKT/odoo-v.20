import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { WebClient } from "@web/webclient/webclient";

patch(WebClient.prototype, {
    setup() {
        super.setup();
        this.homeMenu = useService("home_menu");
        this.pwa = useService("pwa");
    },

    _loadDefaultApp() {
        // A scoped app is dedicated to a single app and has no apps menu.
        if (this.pwa.isScopedApp) {
            return super._loadDefaultApp();
        }
        this.homeMenu.open();
    },
});
