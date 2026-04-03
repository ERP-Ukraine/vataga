/** @odoo-module */

import { patch } from "@web/core/utils/patch";

import { BomOverviewComponent } from "@mrp/components/bom_overview/mrp_bom_overview";
import { BomOverviewControlPanel } from "@mrp/components/bom_overview_control_panel/mrp_bom_overview_control_panel";

const DEFAULT_AVAILABILITY_FILTERS = {
    available: true,
    unavailable: true,
};

const AVAILABILITY_FILTER_LABEL = "Наявність товарів";

function isLineAvailable(line) {
    if (Object.prototype.hasOwnProperty.call(line, "components_available")) {
        return Boolean(line.components_available) && line.availability_state !== "unavailable";
    }
    return line.availability_state === "available";
}

patch(BomOverviewComponent.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.availabilityFilters = { ...DEFAULT_AVAILABILITY_FILTERS };
    },

    onChangeAvailabilityFilter(filterKey) {
        this.state.availabilityFilters[filterKey] = !this.state.availabilityFilters[filterKey];
    },

    get filteredBomData() {
        return this.filterBomData(this.state.bomData, true);
    },

    filterBomData(line, isRoot = false) {
        if (!line || this.hasAllAvailabilityFiltersSelected) {
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
        return isAvailable
            ? this.state.availabilityFilters.available
            : this.state.availabilityFilters.unavailable;
    },

    get hasAllAvailabilityFiltersSelected() {
        return (
            this.state.availabilityFilters.available &&
            this.state.availabilityFilters.unavailable
        );
    },
});

patch(BomOverviewControlPanel.prototype, {
    get availabilityFilterButtonLabel() {
        return AVAILABILITY_FILTER_LABEL;
    },
});

BomOverviewControlPanel.props = {
    ...BomOverviewControlPanel.props,
    currentAvailabilityFilter: {
        type: Object,
        shape: {
            available: Boolean,
            unavailable: Boolean,
        },
        optional: true,
    },
    changeAvailabilityFilter: { type: Function, optional: true },
};

BomOverviewControlPanel.defaultProps = {
    ...BomOverviewControlPanel.defaultProps,
    currentAvailabilityFilter: { ...DEFAULT_AVAILABILITY_FILTERS },
    changeAvailabilityFilter: () => {},
};
