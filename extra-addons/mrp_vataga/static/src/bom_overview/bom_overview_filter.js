/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

import { BomOverviewComponent } from "@mrp/components/bom_overview/mrp_bom_overview";
import { BomOverviewControlPanel } from "@mrp/components/bom_overview_control_panel/mrp_bom_overview_control_panel";

const AVAILABILITY_FILTER_LABELS = {
    all: _t("Наявність"),
    available: _t("В наявності"),
    unavailable: _t("Немає в наявності"),
};

function isLineAvailable(line) {
    if (Object.prototype.hasOwnProperty.call(line, "components_available")) {
        return Boolean(line.components_available) && line.availability_state !== "unavailable";
    }
    return line.availability_state === "available";
}

patch(BomOverviewComponent.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.availabilityFilter = "all";
    },

    onChangeAvailabilityFilter(filterKey) {
        this.state.availabilityFilter =
            this.state.availabilityFilter === filterKey ? "all" : filterKey;
    },

    get filteredBomData() {
        return this.filterBomData(this.state.bomData, true);
    },

    filterBomData(line, isRoot = false) {
        if (!line || this.state.availabilityFilter === "all") {
            return line;
        }

        const filteredComponents = (line.components || [])
            .map((component) => this.filterBomData(component))
            .filter(Boolean);

        if (isRoot) {
            return {
                ...line,
                components: filteredComponents,
            };
        }

        if (filteredComponents.length || this.matchesAvailabilityFilter(line)) {
            return {
                ...line,
                components: filteredComponents,
            };
        }

        return null;
    },

    matchesAvailabilityFilter(line) {
        if (
            !Object.prototype.hasOwnProperty.call(line, "availability_state") &&
            !Object.prototype.hasOwnProperty.call(line, "components_available")
        ) {
            return false;
        }

        const isAvailable = isLineAvailable(line);
        return this.state.availabilityFilter === "available" ? isAvailable : !isAvailable;
    },
});

patch(BomOverviewControlPanel.prototype, {
    get availabilityFilterButtonLabel() {
        return (
            AVAILABILITY_FILTER_LABELS[this.props.currentAvailabilityFilter] ||
            AVAILABILITY_FILTER_LABELS.all
        );
    },
});

BomOverviewControlPanel.props = {
    ...BomOverviewControlPanel.props,
    currentAvailabilityFilter: { type: String, optional: true },
    changeAvailabilityFilter: { type: Function, optional: true },
};

BomOverviewControlPanel.defaultProps = {
    ...BomOverviewControlPanel.defaultProps,
    currentAvailabilityFilter: "all",
    changeAvailabilityFilter: () => {},
};
