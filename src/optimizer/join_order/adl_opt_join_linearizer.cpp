//===----------------------------------------------------------------------===//
//                         DuckDB
//
// duckdb/optimizer/join_order/adl_opt_join_linearizer.cpp
//
//===----------------------------------------------------------------------===//

#include "duckdb/optimizer/join_order/adl_opt_join_linearizer.hpp"

#include "duckdb/common/constants.hpp"
#include "duckdb/common/enums/join_type.hpp"
#include "duckdb/common/string_util.hpp"
#include "duckdb/optimizer/join_order/query_graph_manager.hpp"
#include "yyjson.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace duckdb {

using namespace duckdb_yyjson; // NOLINT

static constexpr double ADL_OPT_INFINITE_RANK = 1e300;

struct ADLOptLinearEdge {
	idx_t edge_id;
	idx_t left;
	idx_t right;
	idx_t filter_index;
	double left_cardinality;
	double right_cardinality;
	double pair_cardinality;
	double selectivity;
	double rank;
	bool mst_edge = false;
};

struct ADLOptLinearOrder {
	idx_t order_id;
	idx_t root;
	double score;
	vector<idx_t> relation_order;
	vector<double> rank_trace;
};

struct ADLOptUnionFind {
	explicit ADLOptUnionFind(idx_t count) : parent(count), rank(count, 0) {
		for (idx_t i = 0; i < count; i++) {
			parent[i] = i;
		}
	}

	idx_t Find(idx_t value) {
		if (parent[value] != value) {
			parent[value] = Find(parent[value]);
		}
		return parent[value];
	}

	bool Union(idx_t left, idx_t right) {
		auto left_parent = Find(left);
		auto right_parent = Find(right);
		if (left_parent == right_parent) {
			return false;
		}
		if (rank[left_parent] < rank[right_parent]) {
			std::swap(left_parent, right_parent);
		}
		parent[right_parent] = left_parent;
		if (rank[left_parent] == rank[right_parent]) {
			rank[left_parent]++;
		}
		return true;
	}

	vector<idx_t> parent;
	vector<idx_t> rank;
};

static yyjson_mut_val *StringArray(yyjson_mut_doc *doc, const vector<string> &items) {
	auto result = yyjson_mut_arr(doc);
	for (auto &item : items) {
		yyjson_mut_arr_add_strcpy(doc, result, item.c_str());
	}
	return result;
}

static yyjson_mut_val *IndexArray(yyjson_mut_doc *doc, const vector<idx_t> &items) {
	auto result = yyjson_mut_arr(doc);
	for (auto &item : items) {
		yyjson_mut_arr_add_uint(doc, result, item);
	}
	return result;
}

static yyjson_mut_val *DoubleArray(yyjson_mut_doc *doc, const vector<double> &items) {
	auto result = yyjson_mut_arr(doc);
	for (auto &item : items) {
		yyjson_mut_arr_add_real(doc, result, item);
	}
	return result;
}

static string JsonToString(yyjson_mut_doc *doc, yyjson_mut_val *root, bool pretty) {
	yyjson_mut_doc_set_root(doc, root);
	size_t len;
	auto flags = pretty ? YYJSON_WRITE_PRETTY : YYJSON_WRITE_NOFLAG;
	auto json = yyjson_mut_write(doc, flags, &len);
	if (!json) {
		yyjson_mut_doc_free(doc);
		// The linearizer is export-only. Serialization failure should surface as metadata instead of failing the
		// user query whose plan is still owned by DuckDB's optimizer.
		return "{\"status\":\"export_error\",\"error\":\"json_serialization_failed\"}";
	}
	string result(json, len);
	free(json);
	yyjson_mut_doc_free(doc);
	return result;
}

static string RelationLabel(idx_t relation_id) {
	// TODO(adl-opt): expose a user-visible SQL alias/table mapping when the external runner starts consuming these
	// orders. For now rN is the DuckDB join-order relation id internal label.
	return StringUtil::Format("r%llu", relation_id);
}

static JoinRelationSet &GetPairRelation(JoinRelationSetManager &set_manager, idx_t left, idx_t right) {
	unordered_set<RelationIndex> bindings;
	bindings.insert(RelationIndex(left));
	bindings.insert(RelationIndex(right));
	return set_manager.GetJoinRelation(bindings);
}

//! Converts DuckDB filter bindings into the only graph shape R5 consumes today:
//! pairwise INNER edges between singleton relation sets. More general DuckDB
//! hypergraph/constraint handling is future ADL-OPT work, not part of this toy
//! IKKBZ export path.
static bool BuildRegularInnerEdges(QueryGraphManager &query_graph_manager, CostModel &cost_model,
                                   vector<ADLOptLinearEdge> &edges, string &unsupported_reason) {
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	auto relation_stats = query_graph_manager.relation_manager.GetRelationStats();
	for (auto &filter : query_graph_manager.GetFilterBindings()) {
		if (filter->set.get().count <= 1) {
			continue;
		}
		if (filter->join_type != JoinType::INNER) {
			unsupported_reason = "non_inner_join";
			return false;
		}
		if (!filter->left_set || !filter->right_set || filter->left_set->count != 1 || filter->right_set->count != 1) {
			unsupported_reason = "hyper_edge_or_non_regular_edge";
			return false;
		}
		auto left = filter->left_set->relations[0].index;
		auto right = filter->right_set->relations[0].index;
		if (left == right || left >= relation_count || right >= relation_count) {
			unsupported_reason = "invalid_edge_relation";
			return false;
		}
		if (left > right) {
			std::swap(left, right);
		}
		auto &pair_set = GetPairRelation(query_graph_manager.set_manager, left, right);
		auto pair_cardinality = cost_model.cardinality_estimator.EstimateCardinalityWithSet<double>(pair_set);
		auto left_cardinality = static_cast<double>(relation_stats[left].cardinality);
		auto right_cardinality = static_cast<double>(relation_stats[right].cardinality);
		auto denom = left_cardinality * right_cardinality;
		auto selectivity = denom <= 0 || pair_cardinality <= 0 ? 0.0 : pair_cardinality / denom;
		if (!std::isfinite(selectivity)) {
			selectivity = 1.0;
		}
		if (selectivity < 0) {
			selectivity = 0.0;
		}
		if (selectivity > 1.0) {
			selectivity = 1.0;
		}
		auto reduction = 1.0 - selectivity;
		// A selectivity of one does not reduce the input at all. Treat it as an effectively infinite Cout rank so it
		// sorts after selective edges without introducing infinities into JSON output.
		auto rank = reduction <= 0 ? ADL_OPT_INFINITE_RANK : MaxValue<double>(pair_cardinality, 0.0) / reduction;
		if (!std::isfinite(rank)) {
			rank = ADL_OPT_INFINITE_RANK;
		}
		ADLOptLinearEdge edge;
		edge.edge_id = edges.size();
		edge.left = left;
		edge.right = right;
		edge.filter_index = filter->filter_index;
		edge.left_cardinality = left_cardinality;
		edge.right_cardinality = right_cardinality;
		edge.pair_cardinality = pair_cardinality;
		edge.selectivity = selectivity;
		edge.rank = rank;
		edges.push_back(edge);
	}
	return true;
}

//! Marks a selectivity minimum spanning tree over the regular edge list. The MST is only
//! the acyclic seed graph required by IKKBZ-style ordering; DuckDB's original query graph
//! and chosen plan are left untouched.
static bool MarkMSTEdges(vector<ADLOptLinearEdge> &edges, idx_t relation_count) {
	vector<idx_t> edge_ids;
	edge_ids.reserve(edges.size());
	for (idx_t i = 0; i < edges.size(); i++) {
		edge_ids.push_back(i);
	}
	std::sort(edge_ids.begin(), edge_ids.end(), [&](idx_t left_id, idx_t right_id) {
		auto &left = edges[left_id];
		auto &right = edges[right_id];
		if (left.selectivity != right.selectivity) {
			return left.selectivity < right.selectivity;
		}
		if (left.left != right.left) {
			return left.left < right.left;
		}
		if (left.right != right.right) {
			return left.right < right.right;
		}
		return left.filter_index < right.filter_index;
	});
	ADLOptUnionFind uf(relation_count);
	idx_t mst_count = 0;
	for (auto edge_id : edge_ids) {
		auto &edge = edges[edge_id];
		if (uf.Union(edge.left, edge.right)) {
			edge.mst_edge = true;
			mst_count++;
			if (mst_count + 1 == relation_count) {
				break;
			}
		}
	}
	return relation_count == 0 || mst_count + 1 == relation_count;
}

static vector<vector<idx_t>> BuildMSTAdjacency(const vector<ADLOptLinearEdge> &edges, idx_t relation_count) {
	vector<vector<idx_t>> adjacency(relation_count);
	for (auto &edge : edges) {
		if (!edge.mst_edge) {
			continue;
		}
		adjacency[edge.left].push_back(edge.right);
		adjacency[edge.right].push_back(edge.left);
	}
	for (auto &neighbors : adjacency) {
		std::sort(neighbors.begin(), neighbors.end());
		neighbors.erase(std::unique(neighbors.begin(), neighbors.end()), neighbors.end());
	}
	return adjacency;
}

static double EdgeRank(const vector<ADLOptLinearEdge> &edges, idx_t left, idx_t right) {
	if (left > right) {
		std::swap(left, right);
	}
	for (auto &edge : edges) {
		if (edge.left == left && edge.right == right && edge.mst_edge) {
			return edge.rank;
		}
	}
	return 0.0;
}

//! Emits a deterministic DFS order for a selected MST root. Children are visited by the
//! Cout-style rank derived from DuckDB cardinality/selectivity estimates, with relation
//! id as a stable tie-breaker.
static void EmitRootOrder(const vector<ADLOptLinearEdge> &edges, const vector<vector<idx_t>> &adjacency, idx_t root,
                          idx_t parent, vector<idx_t> &order, vector<double> &rank_trace) {
	order.push_back(root);
	vector<idx_t> children;
	for (auto child : adjacency[root]) {
		if (child != parent) {
			children.push_back(child);
		}
	}
	std::sort(children.begin(), children.end(), [&](idx_t left, idx_t right) {
		auto left_rank = EdgeRank(edges, root, left);
		auto right_rank = EdgeRank(edges, root, right);
		if (left_rank != right_rank) {
			return left_rank < right_rank;
		}
		return left < right;
	});
	for (auto child : children) {
		rank_trace.push_back(EdgeRank(edges, root, child));
		EmitRootOrder(edges, adjacency, child, root, order, rank_trace);
	}
}

//! Builds the k-best candidates used by R5 today: run the root-order traversal once from
//! every relation, score the resulting rank trace, and keep the requested top-k roots.
//! This is not near-MST or perturbation-based k-best yet.
static vector<ADLOptLinearOrder> BuildRootOrders(const vector<ADLOptLinearEdge> &edges,
                                                 const vector<vector<idx_t>> &adjacency, idx_t relation_count,
                                                 idx_t requested_k) {
	vector<ADLOptLinearOrder> orders;
	orders.reserve(relation_count);
	for (idx_t root = 0; root < relation_count; root++) {
		ADLOptLinearOrder order;
		order.order_id = root;
		order.root = root;
		EmitRootOrder(edges, adjacency, root, DConstants::INVALID_INDEX, order.relation_order, order.rank_trace);
		double score = 0.0;
		for (idx_t i = 0; i < order.rank_trace.size(); i++) {
			score += order.rank_trace[i] * static_cast<double>(order.rank_trace.size() - i);
		}
		order.score = score;
		orders.push_back(std::move(order));
	}
	std::sort(orders.begin(), orders.end(), [](const ADLOptLinearOrder &left, const ADLOptLinearOrder &right) {
		if (left.score != right.score) {
			return left.score < right.score;
		}
		return left.root < right.root;
	});
	if (orders.size() > requested_k) {
		orders.resize(requested_k);
	}
	for (idx_t i = 0; i < orders.size(); i++) {
		orders[i].order_id = i;
	}
	return orders;
}

static vector<string> RelationLabels(const vector<idx_t> &relation_order) {
	vector<string> result;
	result.reserve(relation_order.size());
	for (auto relation : relation_order) {
		result.push_back(RelationLabel(relation));
	}
	return result;
}

static void AddRelationsJSON(yyjson_mut_doc *doc, yyjson_mut_val *root, QueryGraphManager &query_graph_manager) {
	auto relation_stats = query_graph_manager.relation_manager.GetRelationStats();
	auto relations = yyjson_mut_arr(doc);
	for (idx_t i = 0; i < relation_stats.size(); i++) {
		auto relation = yyjson_mut_obj(doc);
		yyjson_mut_obj_add_uint(doc, relation, "relation_id", i);
		auto label = RelationLabel(i);
		yyjson_mut_obj_add_strcpy(doc, relation, "internal_label", label.c_str());
		yyjson_mut_obj_add_uint(doc, relation, "base_cardinality", relation_stats[i].cardinality);
		yyjson_mut_arr_add_val(relations, relation);
	}
	yyjson_mut_obj_add_val(doc, root, "relations", relations);
}

static void AddEdgesJSON(yyjson_mut_doc *doc, yyjson_mut_val *root, const vector<ADLOptLinearEdge> &edges) {
	auto edges_json = yyjson_mut_arr(doc);
	for (auto &edge : edges) {
		auto item = yyjson_mut_obj(doc);
		yyjson_mut_obj_add_uint(doc, item, "edge_id", edge.edge_id);
		yyjson_mut_obj_add_uint(doc, item, "left_relation_id", edge.left);
		yyjson_mut_obj_add_uint(doc, item, "right_relation_id", edge.right);
		yyjson_mut_obj_add_str(doc, item, "join_type", "INNER");
		yyjson_mut_obj_add_uint(doc, item, "filter_index", edge.filter_index);
		yyjson_mut_obj_add_real(doc, item, "estimated_pair_cardinality", edge.pair_cardinality);
		yyjson_mut_obj_add_real(doc, item, "selectivity", edge.selectivity);
		yyjson_mut_obj_add_real(doc, item, "cout_rank", edge.rank);
		yyjson_mut_obj_add_bool(doc, item, "mst_edge", edge.mst_edge);
		yyjson_mut_arr_add_val(edges_json, item);
	}
	yyjson_mut_obj_add_val(doc, root, "edges", edges_json);
}

static void AddOrdersJSON(yyjson_mut_doc *doc, yyjson_mut_val *root, const vector<ADLOptLinearOrder> &orders) {
	auto orders_json = yyjson_mut_arr(doc);
	for (auto &order : orders) {
		auto item = yyjson_mut_obj(doc);
		auto order_id = StringUtil::Format("ikkbz_root_%llu", order.root);
		auto labels = RelationLabels(order.relation_order);
		yyjson_mut_obj_add_strcpy(doc, item, "linear_order_id", order_id.c_str());
		yyjson_mut_obj_add_val(doc, item, "order", StringArray(doc, labels));
		yyjson_mut_obj_add_strcpy(doc, item, "order_id", order_id.c_str());
		yyjson_mut_obj_add_uint(doc, item, "root_relation_id", order.root);
		yyjson_mut_obj_add_real(doc, item, "score", order.score);
		yyjson_mut_obj_add_val(doc, item, "relation_id_order", IndexArray(doc, order.relation_order));
		yyjson_mut_obj_add_val(doc, item, "relation_label_order", StringArray(doc, labels));
		yyjson_mut_obj_add_val(doc, item, "estimated_cout_rank_trace", DoubleArray(doc, order.rank_trace));
		yyjson_mut_arr_add_val(orders_json, item);
	}
	yyjson_mut_obj_add_val(doc, root, "linear_orders", orders_json);
}

static string BuildSummary(const string &status, idx_t relation_count, idx_t k_emitted, const string &selected_order_id,
                           const string &unsupported_reason) {
	auto doc = yyjson_mut_doc_new(nullptr);
	auto root = yyjson_mut_obj(doc);
	yyjson_mut_obj_add_strcpy(doc, root, "status", status.c_str());
	yyjson_mut_obj_add_uint(doc, root, "relation_count", relation_count);
	yyjson_mut_obj_add_uint(doc, root, "k_emitted", k_emitted);
	if (!selected_order_id.empty()) {
		yyjson_mut_obj_add_strcpy(doc, root, "selected_order_id", selected_order_id.c_str());
	}
	if (!unsupported_reason.empty()) {
		yyjson_mut_obj_add_strcpy(doc, root, "unsupported_reason", unsupported_reason.c_str());
	}
	return JsonToString(doc, root, false);
}

//! Builds both the full JSON export and the compact EXPLAIN summary. The status tells
//! consumers whether this optimizer scope produced usable orders, was below the large
//! join threshold, or failed the concentrated regular-inner guard above.
static ADLOptJoinLinearizationResult BuildResult(QueryGraphManager &query_graph_manager, const string &status,
                                                 idx_t requested_k, const string &unsupported_reason,
                                                 const vector<ADLOptLinearEdge> &edges,
                                                 const vector<ADLOptLinearOrder> &orders) {
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	string selected_order_id;
	if (!orders.empty()) {
		selected_order_id = StringUtil::Format("ikkbz_root_%llu", orders[0].root);
	}

	auto doc = yyjson_mut_doc_new(nullptr);
	auto root = yyjson_mut_obj(doc);
	yyjson_mut_obj_add_uint(doc, root, "version", 1);
	yyjson_mut_obj_add_strcpy(doc, root, "status", status.c_str());
	yyjson_mut_obj_add_uint(doc, root, "relation_count", relation_count);
	yyjson_mut_obj_add_uint(doc, root, "large_join_threshold", PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE);
	yyjson_mut_obj_add_uint(doc, root, "k_requested", requested_k);
	yyjson_mut_obj_add_uint(doc, root, "k_emitted", orders.size());
	if (!unsupported_reason.empty()) {
		yyjson_mut_obj_add_strcpy(doc, root, "unsupported_reason", unsupported_reason.c_str());
	}
	if (!selected_order_id.empty()) {
		yyjson_mut_obj_add_strcpy(doc, root, "selected_order_id", selected_order_id.c_str());
	}
	AddRelationsJSON(doc, root, query_graph_manager);
	AddEdgesJSON(doc, root, edges);
	AddOrdersJSON(doc, root, orders);

	ADLOptJoinLinearizationResult result;
	result.status = status;
	result.relation_count = relation_count;
	result.full_json = JsonToString(doc, root, true);
	result.summary_json = BuildSummary(status, relation_count, orders.size(), selected_order_id, unsupported_reason);
	return result;
}

//! Public entrypoint used by JoinOrderOptimizer after DuckDB has solved its native plan.
//! It is export-only: it reads query graph/cardinality metadata and never mutates
//! PlanEnumerator::plans or the logical plan reconstruction path.
ADLOptJoinLinearizationResult ADLOptJoinLinearizer::Generate(QueryGraphManager &query_graph_manager,
                                                             CostModel &cost_model, idx_t requested_k) {
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	if (requested_k == 0) {
		requested_k = 1;
	}
	if (relation_count < PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE) {
		return BuildResult(query_graph_manager, "skipped_not_large_join", requested_k, "", {}, {});
	}

	vector<ADLOptLinearEdge> edges;
	string unsupported_reason;
	if (!BuildRegularInnerEdges(query_graph_manager, cost_model, edges, unsupported_reason)) {
		return BuildResult(query_graph_manager, "unsupported", requested_k, unsupported_reason, edges, {});
	}
	if (!MarkMSTEdges(edges, relation_count)) {
		return BuildResult(query_graph_manager, "unsupported", requested_k, "disconnected_regular_graph", edges, {});
	}

	auto adjacency = BuildMSTAdjacency(edges, relation_count);
	auto emit_k = MinValue<idx_t>(requested_k, relation_count);
	auto orders = BuildRootOrders(edges, adjacency, relation_count, emit_k);
	return BuildResult(query_graph_manager, "ok", requested_k, "", edges, orders);
}

} // namespace duckdb
