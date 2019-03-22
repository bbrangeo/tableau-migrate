#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tableauserverclient as TSC
import os, re
import os.path
import zipfile
import tempfile
import urllib3
import xml.etree.ElementTree as ET
import yaml

paramsFile = 'params.yml'
localParams = 'params_local.yml'

if (os.path.isfile(localParams)):
    with open(localParams, 'r') as f:
        params = yaml.load(f)
else:
    with open(paramsFile, 'r') as f:
        params = yaml.load(f)

print("Executing with params : {}".format(params))

passwords = params["db"]["passwords"]

tableau_servers = {}
tableau_servers["in"] = params["servers"]["in"]
tableau_servers["out"] = params["servers"]["out"]

tableau_servers["in"]["auth"] = TSC.TableauAuth(tableau_servers["in"]["user"], 
                                                tableau_servers["in"]["password"], 
                                                site_id = tableau_servers["in"]["site_id"])
tableau_servers["in"]["server"] = TSC.Server(tableau_servers["in"]["url"])
# api >=3.2 has to be set in order to 
tableau_servers["in"]["server"].version = '3.2'
# insecure mode if problem with https certificates
tableau_servers["in"]["server"].add_http_options({'verify': False})


tableau_servers["out"]["auth"] = TSC.TableauAuth(tableau_servers["out"]["user"], 
                                                tableau_servers["out"]["password"], 
                                                site_id = tableau_servers["out"]["site_id"])
tableau_servers["out"]["server"] = TSC.Server(tableau_servers["out"]["url"])
tableau_servers["out"]["server"].version = '3.2'
tableau_servers["out"]["server"].add_http_options({'verify': False})

replace_connector_values = {
   "xml:base": tableau_servers["out"]["url"],
   "named-connection caption": tableau_servers["out"]["db"],
   "server": tableau_servers["out"]["db"],
   "username": "tableau_er_stats"
}

ipath="tmp"

pmap = {}
dmap = {}

try:
    os.mkdir(ipath)
except:
    pass

def updateTDSX(tdsx):
    # update tdsx, ie tableau datasource (connector) with replace_connector_values
    # generate a temp file
    tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(tdsx))
    os.close(tmpfd)

    # create a temp copy of the archive without filename
    with zipfile.ZipFile(tdsx, 'r') as zin:
        with zipfile.ZipFile(tmpname, 'w') as zout:
            zout.comment = zin.comment # preserve the comment
            for item in zin.infolist():
                xmlstring = zin.read(item.filename).decode('utf-8')
                # print(xmlstring)
                # tree = et.parse(xmlstring)
                # tree.find('@xml:base').text = connector_url
                for key in replace_connector_values.keys(): 
                    print('replace {} value with {}'.format(key, replace_connector_values[key]))
                    pattern = re.compile(r'(\s*)' + key + "='\S*.(\s*)'")
                    xmlstring = re.sub(pattern, r'\1' + key + "='" + replace_connector_values[key] + r"' ", xmlstring)
                zout.writestr(item, xmlstring.encode('utf-8'))

    # replace with the temp archive
    os.remove(tdsx)
    os.rename(tmpname, tdsx)

def migrate_datasources(input="in", output="out"):
    with tableau_servers[input]["server"].auth.sign_in(tableau_servers[input]["auth"]):
        with tableau_servers[output]["server"].auth.sign_in(tableau_servers[output]["auth"]):
            all_datasources, pagination_item = tableau_servers[input]["server"].datasources.get()
            print("\nThere are {} datasources on site: ".format(pagination_item.total_available))
            for datasource in all_datasources:
                try:
                    os.mkdir(os.path.join(ipath, datasource.project_id))
                except:
                    pass
                tdsx = os.path.join(ipath, datasource.project_id, datasource.id + '.tdsx')
                print("datasource: {} > {}".format(datasource.name, tdsx))
                datasource.project_id = pmap[datasource.project_id]
                tableau_servers[input]["server"].datasources.download(datasource.id, filepath = tdsx)
                print('patching {} to output server'.format(tdsx))
                updateTDSX(tdsx)
                tableau_servers[input]["server"].datasources.populate_connections(datasource)
                cc = TSC.ConnectionCredentials(datasource.connections[0].username, passwords[datasource.connections[0].username], embed = True, oauth = False)
                in_id = datasource.id
                out_id = tableau_servers[output]["server"].datasources.publish(datasource, tdsx, TSC.Server.PublishMode.Overwrite, connection_credentials = cc).id
                dmap[in_id] = out_id

def migrate_projects(input="in", output="out"):
    with tableau_servers[input]["server"].auth.sign_in(tableau_servers[input]["auth"]):
        with tableau_servers[output]["server"].auth.sign_in(tableau_servers[output]["auth"]):
            all_projects, pagination_item = tableau_servers[input]["server"].projects.get()
            print("\nThere are {} projects on site: ".format(pagination_item.total_available))
            while (len(all_projects)>0):
                print("remaining {} projects to migrate".format(len(all_projects)))
                project = all_projects.pop(0)
                #if ((project.name != "Default") & (project.name != "Par défaut")):
                in_id = project.id
                if (project.parent_id == None):
                    print("migrate project: {} {} {}".format(project.name, project.id, project.parent_id))
                    out_id = tableau_servers[output]["server"].projects.create(project).id
                    pmap[in_id] = out_id
                else:
                    if (project.parent_id in pmap.keys()):
                        project.parent_id = pmap[project.parent_id]
                        print("migrate project: {} {} {}".format(project.name, project.id, project.parent_id))
                        out_id = tableau_servers[output]["server"].projects.create(project).id
                        pmap[in_id] = out_id
                    else:
                        print("unable to migrate project: {} {} {}".format(project.name, project.id, project.parent_id))
                        all_projects.append(project)
                        
                            

def migrate_workbooks(input="in", output="out"):
    with tableau_servers[input]["server"].auth.sign_in(tableau_servers[input]["auth"]):
        with tableau_servers[output]["server"].auth.sign_in(tableau_servers[output]["auth"]):
            all_workbooks, pagination_item = tableau_servers[input]["server"].workbooks.get()
            print("\nThere are {} workbooks on site: ".format(pagination_item.total_available))
            for workbook in all_workbooks:
                try:
                    os.mkdir(os.path.join(ipath, workbook.project_id))
                except:
                    pass
                twb = os.path.join(ipath, workbook.project_id, workbook.id + '.twb')
                print("workbook: {} > {}".format(workbook.name, twb))
                tableau_servers[input]["server"].workbooks.download(workbook.id, filepath = twb)
                try:
                    workbook.project_id = pmap[workbook.project_id]
                    tableau_servers[output]["server"].workbooks.publish(workbook, twb, TSC.Server.PublishMode.CreateNew)
                # tableau_servers[input]["server"].workbooks.populate_connections(workbook)
                # cc = TSC.ConnectionCredentials(workbook.connections[0].username, passwords[workbook.connections[0].username], embed = True, oauth = False)
                    # tableau_servers[output]["server"].workbooks.publish(workbook, twb, TSC.Server.PublishMode.Overwrite, connection_credentials = cc)
                    tableau_servers[output]["server"].workbooks.publish(workbook, twb, TSC.Server.PublishMode.Overwrite)
                # try:
                except Exception as e:
                    print("WARNING: {}".format(e))
                    
def delete_site(server="out"):
    with tableau_servers[server]["server"].auth.sign_in(tableau_servers[server]["auth"]):
        try:
            site_id = tableau_servers[server]["server"].sites.get_by_name(tableau_servers[server]["site_name"]).id
            tableau_servers[server]["server"].sites.delete(site_id)
        except Exception as e:
            print("WARNING: {}".format(e))

def create_site(server="out"):
    new_site = TSC.SiteItem(name=tableau_servers[server]["site_name"], 
                            content_url=tableau_servers[server]["site_id"])
    tableau_servers["out"]["auth"] = TSC.TableauAuth(tableau_servers["out"]["user"], 
                                                tableau_servers["out"]["password"])
    with tableau_servers[server]["server"].auth.sign_in(tableau_servers[server]["auth"]):
        try:
            tableau_servers[server]["server"].sites.create(new_site)
        except Exception as e:
            print("WARNING: {}".format(e))
    tableau_servers[server]["auth"] = TSC.TableauAuth(tableau_servers[server]["user"], 
                                                tableau_servers[server]["password"], 
                                                site_id = tableau_servers[server]["site_id"])


try:
    delete_site()
except:
    pass
create_site()
migrate_projects()
migrate_datasources()
migrate_workbooks()
