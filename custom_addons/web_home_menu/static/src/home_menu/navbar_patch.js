import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

patch(NavBar.prototype, {
    setup() {
        super.setup();
        this.homeMenu = useService("home_menu");
    },

    _openAppMenuSidebar() {
        if (this.homeMenu.isOpen()) {
            this.homeMenu.close();
            return;
        }
        super._openAppMenuSidebar();
    },

    onAllAppsBtnClick() {
        this._closeAppMenuSidebar();
        this.homeMenu.open();
    },
});
