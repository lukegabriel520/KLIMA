// Power Query M — Hourly aggregates
let
    Source = PostgreSQL.Database(
        "aws-0-ap-southeast-1.pooler.supabase.com",
        "postgres",
        [
            CreateNavigationProperties = true,
            HierarchicalNavigation = true
        ]
    ),
    KlimaSchema = Source{[Name="klima", Kind="Schema"]}[Data],
    HourlyView = KlimaSchema{[Name="vw_powerbi_hourly", Kind="View"]}[Data],
    Typed = Table.TransformColumnTypes(
        HourlyView,
        {
            {"site_id", Int64.Type},
            {"site_name", type text},
            {"parameter", type text},
            {"hour_start", type datetimezone},
            {"hour_start_manila", type datetime},
            {"avg_value", type number},
            {"min_value", type number},
            {"max_value", type number},
            {"sample_count", Int64.Type}
        }
    )
in
    Typed
