/* Runtime compatibility for orphaned room-furniture references.
 * This only makes an optional collect-master lookup nullable. No player data or server requests are changed.
 */
(function (global) {
    "use strict";
    function install(resourceVersion) {
        var ctor = global.__require("RoomFurnitureModel");
        var proto = ctor && ctor.prototype;
        if (!proto || typeof proto.getFurnitureCollectMaster !== "function" ||
            typeof proto.getUserFurnitureModel !== "function" || typeof proto.getCurrentStackCount !== "function") {
            console.log("[DeepOneMod] Furniture compatibility skipped: model layout changed");
            return false;
        }
        if (proto.__deeponeNullableCollectMaster) { return true; }
        var original = proto.getFurnitureCollectMaster;
        if (String(original).indexOf("getUserFurnitureModel") < 0 ||
            String(proto.getCurrentStackCount).indexOf("getFurnitureCollectMaster") < 0) {
            console.log("[DeepOneMod] Furniture compatibility skipped: getter contract changed");
            return false;
        }
        var reported = false;
        proto.getFurnitureCollectMaster = function () {
            if (!this.getUserFurnitureModel()) {
                if (!reported) {
                    reported = true;
                    console.log("[DeepOneMod] Ignored orphaned furniture collect reference; player layout retained");
                }
                return null;
            }
            return original.apply(this, arguments);
        };
        proto.__deeponeNullableCollectMaster = true;
        console.log("[DeepOneMod] Furniture compatibility installed for resource version " + resourceVersion);
        return true;
    }
    global.DeepOneModCompatibility = { install: install };
})(window);
