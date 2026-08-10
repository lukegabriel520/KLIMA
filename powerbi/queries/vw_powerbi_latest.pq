// Power Query M — Latest weather view
// Get Data → Blank Query → Advanced Editor → paste
// Replace credentials via PostgreSQL connector UI when prompted.

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
    LatestView = KlimaSchema{[Name="vw_powerbi_latest", Kind="View"]}[Data],
    Typed = Table.TransformColumnTypes(
        LatestView,
        {
            {"site_id", Int64.Type},
            {"site_name", type text},
            {"latitude", type number},
            {"longitude", type number},
            {"parameter", type text},
            {"parameter_name", type text},
            {"observed_at", type datetimezone},
            {"observed_at_manila", type datetime},
            {"value", type number},
            {"unit", type text},
            {"station_status", type text}
        }
    )
in
    Typed
