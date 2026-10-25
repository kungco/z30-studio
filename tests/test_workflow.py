import tempfile
from pathlib import Path
import unittest
import photo_workflow as workflow
import projects
import server


class WorkflowTests(unittest.TestCase):
    def test_explicit_groups_copy_nested_edits_without_touching_other_settings(self):
        base=server.settings(dict(rotation=2,exposure=.1,masks=[dict(type='radial',exposure=.3)]))
        source=server.settings(dict(exposure=.8,curves={'r':[[0,0],[.5,.6],[1,1]]},masks=[dict(type='brush',points=[[.1,.2]])]))
        target=workflow.transfer(base,source,['light','color'])
        self.assertEqual(target['rotation'],2);self.assertEqual(target['masks'],base['masks'])
        self.assertEqual(target['exposure'],.8)
        target['curves']['r'][1][1]=.9;target['masks'][0]['exposure']=1
        self.assertEqual(source['curves']['r'][1][1],.6);self.assertEqual(base['masks'][0]['exposure'],.3)
        with self.assertRaises(ValueError):workflow.transfer(base,source,['unknown'])

    def test_catalog_validation_and_project_versions_preserve_metadata(self):
        value=workflow.catalog(dict(rating=4,flag='pick',keywords=[' 人像 ','旅行','旅行']))
        self.assertEqual(value['keywords'],['人像','旅行'])
        for bad in ({'rating':True},{'rating':6},{'flag':'delete'},{'keywords':['x'*49]}):
            with self.assertRaises(ValueError):workflow.catalog(bad)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'source.png';original.write_bytes(b'original')
            sources=[dict(path=original,name=original.name,meta={'id':'source'})]
            document=projects.manifest(sources,{'source':dict(catalog=value)},0,server.settings)
            self.assertEqual(document['version'],2)
            path=root/'project.z30proj';projects.write_project(path,sources,document)
            with projects.read_project(path,root,server.settings) as restored:
                self.assertEqual(restored['photos'][0]['catalog'],value)
            document['version']=1;document['photos'][0].pop('catalog');projects.write_project(path,sources,document)
            with projects.read_project(path,root,server.settings) as restored:
                self.assertEqual(restored['photos'][0]['catalog']['rating'],0)
