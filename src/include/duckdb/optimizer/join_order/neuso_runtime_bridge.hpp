//===----------------------------------------------------------------------===//
//                         DuckDB
//
// duckdb/optimizer/join_order/neuso_runtime_bridge.hpp
//
//===----------------------------------------------------------------------===//

#pragma once

#include "duckdb/common/common.hpp"
#include "duckdb/common/vector.hpp"

namespace duckdb {

class ClientContext;
class CostModel;
struct DBConfig;
class QueryGraphManager;

class NeuSORuntimeBridge {
public:
	static void EnsureStarted(ClientContext &context);
	static void EnsureStarted(DBConfig &config);
	static void InvokeIfEnabled(QueryGraphManager &query_graph_manager, CostModel &cost_model);
};

} // namespace duckdb
