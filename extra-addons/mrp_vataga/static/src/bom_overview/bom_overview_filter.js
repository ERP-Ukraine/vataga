/** @odoo-module */

import { patch } from "@web/core/utils/patch";

import { BomOverviewComponent } from "@mrp/components/bom_overview/mrp_bom_overview";
import { BomOverviewControlPanel } from "@mrp/components/bom_overview_control_panel/mrp_bom_overview_control_panel";

const DEFAULT_AVAILABILITY_FILTERS = {
    available: true,
    unavailable: true,
};

const AVAILABILITY_FILTER_LABEL = "Наявність товарів";
const WAREHOUSE_BUTTON_LABEL = "Склад";
const WAREHOUSES_BUTTON_LABEL = "Склади";

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
        this.state.selectedWarehouseIds = [];
        this.lastBomDataRequestId = 0;
    },

    onChangeAvailabilityFilter(filterKey) {
        this.state.availabilityFilters[filterKey] = !this.state.availabilityFilters[filterKey];
    },

    async getBomData() {
        const requestId = ++this.lastBomDataRequestId;
        const selectedWarehouseIds = this.state.selectedWarehouseIds.length
            ? this.state.selectedWarehouseIds
            : this.state.currentWarehouse?.id
              ? [this.state.currentWarehouse.id]
              : [];
        const bomData = await this.orm.call(
            "report.mrp.report_bom_structure",
            "get_html",
            [this.activeId, this.state.bomQuantity, this.state.currentVariantId],
            {
                context: {
                    ...this.context,
                    warehouse_ids: selectedWarehouseIds,
                    warehouse: selectedWarehouseIds[0],
                },
            }
        );

        if (requestId !== this.lastBomDataRequestId) {
            return bomData;
        }

        this.state.bomData = bomData.lines;
        this.state.showOptions.attachments = bomData.has_attachments;
        return bomData;
    },

    async getWarehouses() {
        const warehouses = await this.orm.call(
            "report.mrp.report_bom_structure",
            "get_warehouses"
        );
        this.warehouses = warehouses;
        this.state.currentWarehouse = warehouses[0] || null;
        this.state.selectedWarehouseIds = warehouses[0] ? [warehouses[0].id] : [];
    },

    async onChangeWarehouse(warehouseId) {
        const isSelected = this.state.selectedWarehouseIds.includes(warehouseId);
        const nextSelectedWarehouseIds = isSelected
            ? this.state.selectedWarehouseIds.filter((id) => id !== warehouseId)
            : [...this.state.selectedWarehouseIds, warehouseId];

        if (!nextSelectedWarehouseIds.length) {
            return;
        }

        const hasChanged =
            nextSelectedWarehouseIds.length !== this.state.selectedWarehouseIds.length ||
            nextSelectedWarehouseIds.some(
                (selectedWarehouseId, index) =>
                    selectedWarehouseId !== this.state.selectedWarehouseIds[index]
            );
        if (!hasChanged) {
            return;
        }

        this.state.selectedWarehouseIds = nextSelectedWarehouseIds;
        this.state.currentWarehouse =
            this.warehouses.find((warehouse) => warehouse.id === nextSelectedWarehouseIds[0]) ||
            null;

        await this.getBomData();
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

    get warehouseButtonLabel() {
        const selectedCount = this.props.selectedWarehouseIds.length;
        return selectedCount > 1
            ? `${WAREHOUSES_BUTTON_LABEL} (${selectedCount})`
            : WAREHOUSE_BUTTON_LABEL;
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
    selectedWarehouseIds: { type: Array, optional: true },
    changeAvailabilityFilter: { type: Function, optional: true },
};

BomOverviewControlPanel.defaultProps = {
    ...BomOverviewControlPanel.defaultProps,
    currentAvailabilityFilter: { ...DEFAULT_AVAILABILITY_FILTERS },
    selectedWarehouseIds: [],
    changeAvailabilityFilter: () => {},
};
