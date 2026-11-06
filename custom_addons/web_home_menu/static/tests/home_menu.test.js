import { expect, test } from "@odoo/hoot";
import { animationFrame, press, queryAllTexts, waitFor } from "@odoo/hoot-dom";
import { Component, xml } from "@odoo/owl";
import {
    contains,
    defineActions,
    defineMenus,
    getService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { WebClient } from "@web/webclient/webclient";

class ClientAction extends Component {
    static template = xml`<div class="o_test_client_action"/>`;
}
registry.category("actions").add("home_menu_test_action", ClientAction);

defineActions([
    { id: 1, name: "Action 1", tag: "home_menu_test_action", type: "ir.actions.client" },
]);
defineMenus([
    { id: 1, name: "Sales", actionID: 1, xmlid: "sales" },
    {
        id: 2,
        name: "Inventory",
        actionID: 1,
        xmlid: "inventory",
        children: [{ id: 21, name: "Transfers", actionID: 1, xmlid: "transfers", appID: 2 }],
    },
    { id: 3, name: "Purchase", actionID: 1, xmlid: "purchase" },
]);

async function mountHomeMenu() {
    await mountWithCleanup(WebClient);
    await animationFrame();
    expect(".o_home_menu").toHaveCount(1);
}

/** The search input is out of sight until something is searched. */
async function search(value) {
    await contains(".o_home_menu_search_input", { visible: false }).edit(value, {
        confirm: false,
    });
    await animationFrame();
}

async function openApp(name) {
    await contains(`.o_home_menu_app:contains(${name})`).click();
    await waitFor(".o_test_client_action");
    await animationFrame();
    expect(".o_home_menu").toHaveCount(0);
}

test("is displayed when there is no action to load", async () => {
    await mountHomeMenu();
    expect(queryAllTexts(".o_home_menu_label")).toEqual(["Sales", "Inventory", "Purchase"]);
    expect(document.body).toHaveClass("o_home_menu_background");
    expect(".o_home_menu_toggle").toHaveCount(0);
});

test("clicking on an app opens it", async () => {
    await mountHomeMenu();
    await openApp("Inventory");
    expect(document.body).not.toHaveClass("o_home_menu_background");
    expect(getService("menu").getCurrentApp().id).toBe(2);
});

test.tags("desktop");
test("the toggle goes back to the action without reloading it", async () => {
    await mountHomeMenu();
    await openApp("Sales");
    const actionEl = document.querySelector(".o_test_client_action");
    await contains(".o_home_menu_toggle").click();
    expect(".o_home_menu").toHaveCount(1);
    await contains(".o_home_menu_toggle").click();
    expect(".o_home_menu").toHaveCount(0);
    expect(document.querySelector(".o_test_client_action")).toBe(actionEl);
});

test.tags("desktop");
test("search filters apps and menu items", async () => {
    await mountHomeMenu();
    await search("trans");
    expect(".o_home_menu_app").toHaveCount(0);
    expect(queryAllTexts(".o_home_menu_result")).toEqual(["Inventory / Transfers"]);
    expect(".o_home_menu_result").toHaveClass("o_home_menu_item_focused");

    await search("zzzz");
    expect(".o_home_menu_empty").toHaveCount(1);
});

test.tags("desktop");
test("enter opens the first search result", async () => {
    await mountHomeMenu();
    await search("purch");
    await press("Enter");
    await waitFor(".o_test_client_action");
    await animationFrame();
    expect(".o_home_menu").toHaveCount(0);
    expect(getService("menu").getCurrentApp().id).toBe(3);
});

test.tags("desktop");
test("arrow keys move the selection", async () => {
    await mountHomeMenu();
    const focusedLabel = () => queryAllTexts(".o_home_menu_item_focused .o_home_menu_label");
    await press("ArrowDown");
    await animationFrame();
    expect(focusedLabel()).toEqual(["Sales"]);
    await press("ArrowRight");
    await animationFrame();
    expect(focusedLabel()).toEqual(["Inventory"]);
    await press("ArrowLeft");
    await press("ArrowLeft");
    await animationFrame();
    expect(focusedLabel()).toEqual(["Purchase"]);
});

test.tags("desktop");
test("escape clears the search, then goes back to the action", async () => {
    await mountHomeMenu();
    await openApp("Sales");
    await contains(".o_home_menu_toggle").click();
    await search("sal");
    await press("Escape");
    await animationFrame();
    expect(".o_home_menu_search_input").toHaveValue("");
    expect(".o_home_menu").toHaveCount(1);
    await press("Escape");
    await animationFrame();
    expect(".o_home_menu").toHaveCount(0);
});

test.tags("desktop");
test("escape does nothing when there is no action to go back to", async () => {
    await mountHomeMenu();
    await press("Escape");
    await animationFrame();
    expect(".o_home_menu").toHaveCount(1);
});
