#include "duckdb/optimizer/join_order/join_order_optimizer.hpp"

#include "duckdb/common/limits.hpp"
#include "duckdb/common/pair.hpp"
#include "duckdb/common/serializer/buffered_file_writer.hpp"
#include "duckdb/common/string_util.hpp"
#include "duckdb/optimizer/join_order/cost_model.hpp"
#include "duckdb/optimizer/join_order/adl_opt_join_linearizer.hpp"
#include "duckdb/optimizer/join_order/neuso_runtime_bridge.hpp"
#include "duckdb/optimizer/join_order/plan_enumerator.hpp"
#include "duckdb/planner/expression/list.hpp"
#include "duckdb/planner/operator/list.hpp"
#include "duckdb/main/client_data.hpp"
#include "duckdb/main/settings.hpp"

namespace duckdb {

// The ADL-OPT helpers in this file are intentionally limited to join-order pass glue:
// they honor local ADL settings, write optional export metadata, and expose a compact
// EXPLAIN summary through ClientData. The actual regular-inner-only
// IKKBZ/MST linearization lives in ADLOptJoinLinearizer so the algorithm does not leak
// into DuckDB's native plan enumeration and reconstruction logic.

//! Chooses which recursive optimizer scope should be visible in the single EXPLAIN
//! summary slot. An inner large-join OK result is more useful than a wrapper-level skip,
//! but callers must still treat the summary as one optimizer-scope export, not a complete
//! whole-statement contract.
static idx_t ADLOptJoinLinearizationPriority(const ADLOptJoinLinearizationResult &result) {
	if (result.relation_count <= 1) {
		return 1;
	}
	if (result.status == "ok") {
		return 4;
	}
	if (result.status == "unsupported") {
		return 3;
	}
	if (result.status == "skipped_not_large_join") {
		return 2;
	}
	return 1;
}

static string ADLOptJsonEscape(const string &input) {
	string result;
	for (auto c : input) {
		switch (c) {
		case '\\':
			result += "\\\\";
			break;
		case '"':
			result += "\\\"";
			break;
		case '\n':
			result += "\\n";
			break;
		case '\r':
			result += "\\r";
			break;
		case '\t':
			result += "\\t";
			break;
		default:
			result += c;
			break;
		}
	}
	return result;
}

//! Stores the ADL-OPT export in the two user-visible channels owned by the join-order
//! pass: optional full JSON on disk and compact EXPLAIN metadata in ClientData. Export
//! errors are recorded as metadata because R5 must not fail the DuckDB query plan.
static void StoreADLOptJoinLinearization(ClientContext &context, ADLOptJoinLinearizationResult result) {
	auto &client_data = ClientData::Get(context);
	auto priority = ADLOptJoinLinearizationPriority(result);
	if (!client_data.adl_join_linearization.empty() && priority <= client_data.adl_join_linearization_priority) {
		return;
	}
	client_data.adl_join_linearization = result.summary_json;
	client_data.adl_join_linearization_priority = priority;

	auto output_path = Settings::Get<AdlLinearizationOutputSetting>(context);
	if (output_path.empty()) {
		return;
	}
	try {
		auto &fs = FileSystem::GetFileSystem(context);
		BufferedFileWriter writer(fs, output_path, FileFlags::FILE_FLAGS_WRITE | FileFlags::FILE_FLAGS_FILE_CREATE_NEW);
		writer.WriteData(const_data_ptr_cast(result.full_json.c_str()), result.full_json.size());
		writer.WriteData(const_data_ptr_cast("\n"), 1);
		writer.Close();
	} catch (std::exception &ex) {
		auto escaped_error = ADLOptJsonEscape(ex.what());
		client_data.adl_join_linearization =
		    StringUtil::Format("{\"status\":\"export_error\",\"error\":\"%s\"}", escaped_error);
	}
}

//! Runs the supported regular-inner linearizer for the current reorderable join graph.
static ADLOptJoinLinearizationResult ExportADLOptJoinLinearization(ClientContext &context,
                                                                   QueryGraphManager &query_graph_manager,
                                                                   CostModel &cost_model) {
	if (!Settings::Get<AdlLinearizeJoinOrderSetting>(context)) {
		return ADLOptJoinLinearizationResult();
	}
	auto requested_k = Settings::Get<AdlIkkbzKSetting>(context);
	auto result = ADLOptJoinLinearizer::Generate(query_graph_manager, cost_model, requested_k);
	auto bridge_result = result;
	StoreADLOptJoinLinearization(context, std::move(result));
	return bridge_result;
}

JoinOrderOptimizer::JoinOrderOptimizer(ClientContext &context)
    : context(context), query_graph_manager(context), depth(1) {
}

JoinOrderOptimizer JoinOrderOptimizer::CreateChildOptimizer() {
	JoinOrderOptimizer child_optimizer(context);
	child_optimizer.materialized_cte_stats = materialized_cte_stats;
	child_optimizer.delim_scan_stats = delim_scan_stats;
	child_optimizer.depth = depth + 1;
	child_optimizer.recursive_cte_indexes = recursive_cte_indexes;
	return child_optimizer;
}

unique_ptr<LogicalOperator> JoinOrderOptimizer::Optimize(unique_ptr<LogicalOperator> plan,
                                                         optional_ptr<RelationStats> stats) {
	auto max_expression_depth = Settings::Get<MaxExpressionDepthSetting>(query_graph_manager.context);
	if (depth > max_expression_depth) {
		// Very deep plans will eventually consume quite some stack space
		// Returning the current plan is always a valid choice
		return plan;
	}

	// make sure query graph manager has not extracted a relation graph already
	LogicalOperator *op = plan.get();

	// extract the relations that go into the hyper graph.
	// We optimize the children of any non-reorderable operations we come across.
	bool reorderable = query_graph_manager.Build(*this, *op);

	// get relation_stats here since the reconstruction process will move all relations.
	auto relation_stats = query_graph_manager.relation_manager.GetRelationStats();
	unique_ptr<LogicalOperator> new_logical_plan = nullptr;

	if (reorderable) {
		// query graph now has filters and relations
		auto cost_model = CostModel(query_graph_manager);

		// Initialize a plan enumerator.
		auto plan_enumerator =
		    PlanEnumerator(query_graph_manager, cost_model, query_graph_manager.GetQueryGraphEdges());

		// Initialize the leaf/single node plans
		plan_enumerator.InitLeafPlans();
		auto linearization_result = ExportADLOptJoinLinearization(context, query_graph_manager, cost_model);
		auto adl_join_order =
		    NeuSORuntimeBridge::InvokeIfEnabled(query_graph_manager, cost_model, linearization_result.linear_orders);
		if (!adl_join_order.empty()) {
			plan_enumerator.ApplyJoinOrder(adl_join_order);
		} else {
			plan_enumerator.SolveJoinOrder();
		}
		// now reconstruct a logical plan from the query graph plan
		query_graph_manager.plans = &plan_enumerator.GetPlans();

		new_logical_plan = query_graph_manager.Reconstruct(std::move(plan));
	} else {
		new_logical_plan = std::move(plan);
		if (relation_stats.size() == 1) {
			new_logical_plan->estimated_cardinality = relation_stats.at(0).cardinality;
			new_logical_plan->has_estimated_cardinality = true;
		}
	}

	// Propagate up a stats object from the top of the new_logical_plan if stats exist.
	if (stats) {
		auto cardinality = new_logical_plan->EstimateCardinality(context);
		auto bindings = new_logical_plan->GetColumnBindings();
		auto new_stats = RelationStatisticsHelper::CombineStatsOfReorderableOperator(bindings, relation_stats);
		new_stats.cardinality = cardinality;
		RelationStatisticsHelper::CopyRelationStats(*stats, new_stats);
	} else {
		// starts recursively setting cardinality
		new_logical_plan->EstimateCardinality(context);
	}

	if (new_logical_plan->type == LogicalOperatorType::LOGICAL_EXPLAIN) {
		new_logical_plan->SetEstimatedCardinality(3);
	}

	return new_logical_plan;
}

void JoinOrderOptimizer::AddMaterializedCTEStats(TableIndex index, RelationStats &&stats) {
	materialized_cte_stats.emplace(index, std::move(stats));
}

RelationStats JoinOrderOptimizer::GetMaterializedCTEStats(TableIndex index) {
	auto it = materialized_cte_stats.find(index);
	if (it == materialized_cte_stats.end()) {
		throw InternalException("Unable to find materialized CTE stats with index %llu", index.index);
	}
	return it->second;
}

void JoinOrderOptimizer::AddDelimScanStats(RelationStats &stats) {
	delim_scan_stats = &stats;
}

RelationStats JoinOrderOptimizer::GetDelimScanStats() {
	if (!delim_scan_stats) {
		throw InternalException("Unable to find delim scan stats!");
	}
	return *delim_scan_stats;
}

} // namespace duckdb
