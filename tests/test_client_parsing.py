"""Tests for the SDMX-ML parsers using realistic TNSO-shaped XML (no network)."""

from tnso_mcp_server.api.client import ApiClient

NS = (
    'xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
    'xmlns:structure="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
    'xmlns:common="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"'
)

DATAFLOW_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<message:Structure {NS}>
 <message:Structures><structure:Dataflows>
  <structure:Dataflow id="DF_01DI_IND_AGING" agencyID="TNSO" version="1.0">
    <common:Name xml:lang="th">ชุดข้อมูลดัชนีการสูงอายุ</common:Name>
    <common:Name xml:lang="en">Aging Index dataflow</common:Name>
    <common:Description xml:lang="en">Aging index by area.</common:Description>
    <structure:Structure>
      <Ref id="DSD_AGING" agencyID="TNSO" version="1.0" package="datastructure" class="DataStructure"/>
    </structure:Structure>
  </structure:Dataflow>
 </structure:Dataflows></message:Structures>
</message:Structure>"""

DSD_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<message:Structure {NS}>
 <message:Structures><structure:DataStructures>
  <structure:DataStructure id="DSD_AGING" agencyID="TNSO" version="1.0">
   <structure:DataStructureComponents>
    <structure:DimensionList>
     <structure:Dimension id="CWT" position="2">
       <structure:LocalRepresentation><structure:Enumeration>
         <Ref id="CL_CWT" class="Codelist"/>
       </structure:Enumeration></structure:LocalRepresentation>
     </structure:Dimension>
     <structure:Dimension id="POP_IND" position="1">
       <structure:LocalRepresentation><structure:Enumeration>
         <Ref id="CL_POP_IND" class="Codelist"/>
       </structure:Enumeration></structure:LocalRepresentation>
     </structure:Dimension>
     <structure:TimeDimension id="TIME_PERIOD" position="3"/>
    </structure:DimensionList>
   </structure:DataStructureComponents>
  </structure:DataStructure>
 </structure:DataStructures></message:Structures>
</message:Structure>"""

CODELIST_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<message:Structure {NS}>
 <message:Structures><structure:Codelists>
  <structure:Codelist id="CL_CWT" agencyID="TNSO" version="1.0">
    <structure:Code id="10">
      <common:Name xml:lang="en">Krung Thep Maha Nakhon (Bangkok)</common:Name>
      <common:Name xml:lang="th">กรุงเทพมหานคร</common:Name>
    </structure:Code>
    <structure:Code id="58">
      <common:Name xml:lang="en">Mae Hong Son</common:Name>
      <common:Name xml:lang="th">แม่ฮ่องสอน</common:Name>
    </structure:Code>
  </structure:Codelist>
 </structure:Codelists></message:Structures>
</message:Structure>"""

AVAILABLECONSTRAINT_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<message:Structure {NS}>
 <message:Structures><structure:Constraints>
  <structure:ContentConstraint id="CR" type="Actual">
   <structure:CubeRegion include="true">
     <common:KeyValue id="POP_IND"><common:Value>DEM_IND101</common:Value></common:KeyValue>
     <common:KeyValue id="CWT">
       <common:Value>_T</common:Value>
       <common:Value>10</common:Value>
       <common:Value>58</common:Value>
     </common:KeyValue>
     <common:KeyValue id="TIME_PERIOD">
       <common:TimeRange>
         <common:StartPeriod>2557</common:StartPeriod>
         <common:EndPeriod>2567</common:EndPeriod>
       </common:TimeRange>
     </common:KeyValue>
   </structure:CubeRegion>
  </structure:ContentConstraint>
 </structure:Constraints></message:Structures>
</message:Structure>"""


def test_parse_dataflows():
    dataflows = ApiClient._parse_dataflows(DATAFLOW_XML)
    assert len(dataflows) == 1
    df = dataflows[0]
    assert df.id == "DF_01DI_IND_AGING"
    assert df.name_en == "Aging Index dataflow"
    assert df.name_th.startswith("ชุดข้อมูล")
    assert df.id_datastructure == "DSD_AGING"
    assert df.version == "1.0"
    assert df.agency == "TNSO"


def test_parse_datastructure_orders_by_position_and_keeps_time_last():
    dsd = ApiClient._parse_datastructure("DSD_AGING", DSD_XML)
    assert [d.dimension for d in dsd.dimensions] == ["POP_IND", "CWT", "TIME_PERIOD"]
    by_name = {d.dimension: d.codelist for d in dsd.dimensions}
    assert by_name["CWT"] == "CL_CWT"
    assert by_name["POP_IND"] == "CL_POP_IND"
    assert by_name["TIME_PERIOD"] == ""


def test_parse_codelist_bilingual():
    cl = ApiClient._parse_codelist("CL_CWT", CODELIST_XML)
    by_code = {c.code: c for c in cl.values}
    assert by_code["10"].name_en.startswith("Krung Thep")
    assert by_code["10"].name_th == "กรุงเทพมหานคร"
    assert by_code["58"].name_en == "Mae Hong Son"


def test_parse_availableconstraint():
    dim_values, time_range = ApiClient._parse_availableconstraint(AVAILABLECONSTRAINT_XML)
    assert dim_values["POP_IND"] == ["DEM_IND101"]
    assert dim_values["CWT"] == ["_T", "10", "58"]
    # TIME_PERIOD has no plain <Value>, only a TimeRange -> excluded from dim_values
    assert "TIME_PERIOD" not in dim_values
    assert time_range == ("2557", "2567")
