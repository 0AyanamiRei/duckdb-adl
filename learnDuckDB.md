## first step

这一次我期望从使用Duckdb的SQL tests框架开始, 第一个阶段想要能够单步调试duckdb的sql test命令。

官方的文档：[Debug Duckdb](https://duckdb.org/docs/stable/dev/sqllogictest/debugging)， 为了便于浏览，我记录一些
在本地。

首先，在`make debug`编译下的系统运行。

> it is recommended to only run the test that breaks. This can be done by passing the filename of the breaking test to the test suite as a command line parameter (e.g., `build/debug/test/unittest test/sql/projection/test_simple_projection.test`).

在**sqllogictests**中，通常很难在特定查询上设置断点，但是Duckdb扩展了测试，每次运行查询时候会调用`query_break`，参数是查询所在的行号，允许在指定位置设置断点：（在测试文件第43行处设置断点）

```sh
gdb: break query_break if line==43
lldb: break s -n query_break -c line==43
```

还可以使用`mode skip`+`mode unskip`来跳过中间的查询。

执行指定测试、执行指定目录下所有测试、执行文本中标记的测试：

```sh
./build/debug/test/unittest test/sql/projection/test_simple_projection.test

./build/debug/test/unittest "[test/sql/xxx/mytest]"

cat test.list
test/sql/join/full_outer/test_full_outer_join_issue_4252.test
test/sql/join/full_outer/full_outer_join_cache.test
test/sql/join/full_outer/test_full_outer_join.test

build/debug/test/unittest -f test.list
```

