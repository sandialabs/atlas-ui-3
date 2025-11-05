import xml.etree.ElementTree as ET
from typing import Dict, List
from xml.dom import minidom


def create_list_objects_xml(bucket: str, prefix: str, objects: List[Dict[str, str]], is_truncated: bool = False, continuation_token: str = "") -> str:
    """Generate S3 ListObjectsV2 XML response."""
    root = ET.Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")

    ET.SubElement(root, "Name").text = bucket
    ET.SubElement(root, "Prefix").text = prefix
    ET.SubElement(root, "KeyCount").text = str(len(objects))
    ET.SubElement(root, "MaxKeys").text = "1000"
    ET.SubElement(root, "IsTruncated").text = "true" if is_truncated else "false"
    if continuation_token:
        ET.SubElement(root, "ContinuationToken").text = continuation_token

    for obj in objects:
        contents = ET.SubElement(root, "Contents")
        ET.SubElement(contents, "Key").text = obj["Key"]
        ET.SubElement(contents, "LastModified").text = obj["LastModified"]
        ET.SubElement(contents, "ETag").text = f'"{obj["ETag"]}"'
        ET.SubElement(contents, "Size").text = str(obj["Size"])
        ET.SubElement(contents, "StorageClass").text = "STANDARD"

    # Pretty print XML
    rough_string = ET.tostring(root, encoding='unicode')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")


def create_tagging_xml(tags: Dict[str, str]) -> str:
    """Generate S3 Tagging XML response."""
    root = ET.Element("Tagging")
    tag_set = ET.SubElement(root, "TagSet")

    for key, value in tags.items():
        tag = ET.SubElement(tag_set, "Tag")
        ET.SubElement(tag, "Key").text = key
        ET.SubElement(tag, "Value").text = value

    # Pretty print XML
    rough_string = ET.tostring(root, encoding='unicode')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")


def parse_tagging_xml(xml_content: str) -> Dict[str, str]:
    """Parse S3 Tagging XML input."""
    try:
        root = ET.fromstring(xml_content)
        tags = {}
        tag_set = root.find("TagSet")
        if tag_set is not None:
            for tag in tag_set.findall("Tag"):
                key_elem = tag.find("Key")
                value_elem = tag.find("Value")
                if key_elem is not None and value_elem is not None:
                    key = key_elem.text or ""
                    value = value_elem.text or ""
                    tags[key] = value
        return tags
    except ET.ParseError:
        raise ValueError("Malformed XML")


def create_error_xml(code: str, message: str, resource: str = "") -> str:
    """Generate S3 error XML response."""
    root = ET.Element("Error")
    ET.SubElement(root, "Code").text = code
    ET.SubElement(root, "Message").text = message
    if resource:
        ET.SubElement(root, "Resource").text = resource

    # Pretty print XML
    rough_string = ET.tostring(root, encoding='unicode')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")
