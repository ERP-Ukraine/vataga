/** @odoo-module **/

import {
    DemandPivotLoadCoordinator,
    InitialSearchStateGate,
    loadDemandPivotAfterSearchReady,
} from "@sale_demand_vataga/views/pivot/pivot_view";

function makeSearchParams(overrides = {}) {
    return {
        comparison: null,
        context: {},
        domain: [],
        groupBy: [],
        orderBy: [],
        ...overrides,
    };
}

function makeSearchModel(gate, searchParams) {
    return {
        ...searchParams,
        waitForInitialSearchState: () => gate.wait(),
    };
}

QUnit.module("sale_demand_vataga demand pivot initialization", () => {
    QUnit.test("saved filter is used by the only initial pivot load", async (assert) => {
        const gate = new InitialSearchStateGate();
        const finalDomain = [["product_id", "=", 42]];
        const searchModel = makeSearchModel(
            gate,
            makeSearchParams({ domain: finalDomain })
        );
        const loadCalls = [];
        const coordinator = new DemandPivotLoadCoordinator((searchParams) => {
            loadCalls.push(searchParams);
            return "loaded";
        });

        const pivotLoad = loadDemandPivotAfterSearchReady({
            activeMeasures: ["demand", "closed"],
            coordinator,
            searchModel,
            searchParams: makeSearchParams({ domain: [] }),
        });
        const searchLoad = gate.track(Promise.resolve());

        await searchLoad;
        assert.strictEqual(await pivotLoad, "loaded");
        assert.strictEqual(loadCalls.length, 1);
        assert.deepEqual(loadCalls[0].domain, finalDomain);
        assert.notDeepEqual(loadCalls[0].domain, []);
    });

    QUnit.test("an intentionally empty domain loads once", async (assert) => {
        const gate = new InitialSearchStateGate();
        const searchModel = makeSearchModel(gate, makeSearchParams());
        const loadCalls = [];
        const coordinator = new DemandPivotLoadCoordinator((searchParams) => {
            loadCalls.push(searchParams);
            return "unfiltered";
        });

        const pivotLoad = loadDemandPivotAfterSearchReady({
            activeMeasures: ["demand"],
            coordinator,
            searchModel,
            searchParams: makeSearchParams(),
        });
        await gate.track(Promise.resolve());

        assert.strictEqual(await pivotLoad, "unfiltered");
        assert.strictEqual(loadCalls.length, 1);
        assert.deepEqual(loadCalls[0].domain, []);
    });

    QUnit.test("identical concurrent props share one load", async (assert) => {
        let resolveLoad;
        const backendResult = new Promise((resolve) => {
            resolveLoad = resolve;
        });
        let loadCount = 0;
        const coordinator = new DemandPivotLoadCoordinator(() => {
            loadCount += 1;
            return backendResult;
        });
        const searchParams = makeSearchParams({
            context: { pivot_measures: ["demand", "closed"] },
            domain: [["product_id", "=", 42]],
        });

        const firstLoad = coordinator.load(searchParams, ["demand", "closed"]);
        const secondLoad = coordinator.load(searchParams, ["demand", "closed"]);
        await Promise.resolve();

        assert.strictEqual(loadCount, 1);
        resolveLoad("shared result");
        assert.strictEqual(await firstLoad, "shared result");
        assert.strictEqual(await secondLoad, "shared result");
    });

    QUnit.test("a changed domain starts a new load", async (assert) => {
        const loadCalls = [];
        const coordinator = new DemandPivotLoadCoordinator((searchParams) => {
            loadCalls.push(searchParams);
            return loadCalls.length;
        });

        await coordinator.load(
            makeSearchParams({ domain: [["product_id", "=", 42]] }),
            ["demand"]
        );
        await coordinator.load(
            makeSearchParams({ domain: [["product_id", "=", 84]] }),
            ["demand"]
        );

        assert.strictEqual(loadCalls.length, 2);
        assert.notDeepEqual(loadCalls[0].domain, loadCalls[1].domain);
    });

    QUnit.test("a changed groupBy starts a new load", async (assert) => {
        const loadCalls = [];
        const coordinator = new DemandPivotLoadCoordinator((searchParams) => {
            loadCalls.push(searchParams);
            return loadCalls.length;
        });
        const domain = [["product_id", "=", 42]];

        await coordinator.load(
            makeSearchParams({ domain, groupBy: ["product_id"] }),
            ["demand"]
        );
        await coordinator.load(
            makeSearchParams({
                domain,
                groupBy: ["product_id", "sale_contract_id"],
            }),
            ["demand"]
        );

        assert.strictEqual(loadCalls.length, 2);
        assert.notDeepEqual(loadCalls[0].groupBy, loadCalls[1].groupBy);
    });

    QUnit.test("changed active measures start a new load", async (assert) => {
        let loadCount = 0;
        const coordinator = new DemandPivotLoadCoordinator(() => {
            loadCount += 1;
            return loadCount;
        });
        const searchParams = makeSearchParams({
            domain: [["product_id", "=", 42]],
        });

        await coordinator.load(searchParams, ["demand"]);
        await coordinator.load(searchParams, ["demand", "closed"]);

        assert.strictEqual(loadCount, 2);
    });

    QUnit.test("failed readiness and load caches can be retried", async (assert) => {
        const gate = new InitialSearchStateGate();
        const initialError = new Error("initial search failed");
        const failedSearchLoad = gate.track(Promise.reject(initialError));
        const failedWait = gate.wait();

        await assert.rejects(failedSearchLoad, /initial search failed/);
        await assert.rejects(failedWait, /initial search failed/);

        const retrySearchLoad = gate.track(Promise.resolve("search ready"));
        assert.strictEqual(await retrySearchLoad, "search ready");
        await gate.wait();

        let loadCount = 0;
        const coordinator = new DemandPivotLoadCoordinator(() => {
            loadCount += 1;
            if (loadCount === 1) {
                return Promise.reject(new Error("pivot failed"));
            }
            return "retry succeeded";
        });
        const searchParams = makeSearchParams({
            domain: [["product_id", "=", 42]],
        });

        await assert.rejects(
            coordinator.load(searchParams, ["demand"]),
            /pivot failed/
        );
        assert.strictEqual(
            await coordinator.load(searchParams, ["demand"]),
            "retry succeeded"
        );
        assert.strictEqual(loadCount, 2);
    });
});
