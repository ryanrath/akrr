"""
Python script for retrieving the 'resource' information from XDMoD and pre-generating
the default resource templates.
"""

###############################################################################
# IMPORTS
###############################################################################
import sys
import os
import getpass
import io
import re
import MySQLdb

from akrr import akrrcfg
from akrr import log
from akrr.util import log_input

from . import resource_deploy



###############################################################################
# VARIABLES
###############################################################################

# global variable to hold the script arguments
config_dir = akrrcfg.cfg_dir
resources_dir = os.path.join(config_dir, 'resources')

resourceName=None
verbose=False
dry_run=False
minimalistic=False
no_ping=False

rsh=None
#variables for template with default values
info = None
ppn = None
remoteAccessNode = None
remoteAccessMethod = 'ssh'
remoteCopyMethod='scp'
sshUserName = None
sshPassword = None
sshPassword4thisSession = None
sshPrivateKeyFile = None
sshPrivateKeyPassword=None

networkScratch=None
localScratch="/tmp"
akrrData=None
appKerDir=None
batchScheduler = None
batchJobHeaderTemplate=None

resource_cfg_filename=None


###############################################################################
# SCRIPT FUNCTIONS
###############################################################################

def retrieve_resources():
    """
    Retrieve the applicable contents of the `modw`.`resourcefact` table.
    :return: a tuple of strings containing the name of the resources.
    """
    con, cur = akrrcfg.getXDDB()
        
    if con==None:
        #i.e. AKRR running without modw
        return tuple()
        
    cur.execute("SELECT `name`,`id` FROM `modw`.`resourcefact`")
    rows = cur.fetchall()

    log.debug("Retrieved %s Resource records...", len(rows) if rows else 0)

    return rows

def create_resource_config(file_path, queuing_system):
    """
    create resource file from template
    """
    
    with open(os.path.join(akrrcfg.templates_dir, 'template.%s.conf'%queuing_system)) as fin:
        template=fin.read()
    fin.close()
    
    def update_template(s,variable,inQuotes=True):
        rexp='^'+variable+'\s*=\s*.*$'
        replace=variable+' = '
        value=globals()[variable]
        if value==None:
            replace+='None'
        else:
            if inQuotes:replace+='"'
            replace+=str(value)
            if inQuotes:replace+='"'
        out=[]
        lines=s.splitlines()
        for line in lines:
            out.append(re.sub(rexp,replace,line))
        #s=re.sub(rexp,replace,s,flags=re.M)
        
        return "\n".join(out)
    
    template=update_template(template,'ppn',inQuotes=False)
    for v in ['remoteAccessNode','remoteAccessMethod','remoteCopyMethod',
              'sshUserName','sshPassword','sshPrivateKeyFile','sshPrivateKeyPassword',
              'networkScratch','localScratch','akrrData','appKerDir','batchScheduler']:
        template=update_template(template,v)
    template+="\n\n"

    if not dry_run:
        with open(file_path,'w') as fout:
            fout.write(template)
        fout.close()
    else:
        log.info('Dry Run Mode: Would have written to: %s', file_path)
        log.info('It content would be:')
        print(template)


def generate_resource_config(resource_id, resource_name, queuing_system):
    log.info("Initiating %s at AKRR"%(resource_name,))
    

    if not dry_run:
        os.mkdir(os.path.join(resources_dir, resource_name),0o700)
    
    file_path = os.path.abspath(os.path.join(resources_dir, resource_name, 'resource.conf'))
    global resource_cfg_filename
    resource_cfg_filename=file_path
    
    
    create_resource_config(file_path, queuing_system)
        
    if not dry_run:    
        #add entry to mod_appkernel.resource
        dbAK,curAK=akrrcfg.getAKDB(True)
            
        curAK.execute('''SELECT * FROM resource WHERE nickname=%s''', (resource_name,))
        resource_in_AKDB = curAK.fetchall()
        if len(resource_in_AKDB)==0:
            curAK.execute('''INSERT INTO resource (resource,nickname,description,enabled,visible,xdmod_resource_id)
                        VALUES(%s,%s,%s,0,0,%s);''',
                        (resource_name,resource_name,resource_name,resource_id))
            dbAK.commit()
        curAK.execute('''SELECT * FROM resource WHERE nickname=%s''', (resource_name,))
        resource_in_AKDB = curAK.fetchall()
        resource_id_in_AKDB=resource_in_AKDB[0]['resource_id']
        #add entry to mod_akrr.resource
        db,cur=akrrcfg.getDB(True)
            
        cur.execute('''SELECT * FROM resources WHERE name=%s''', (resource_name,))
        resource_in_DB = cur.fetchall()
        if len(resource_in_DB)==0:
            cur.execute('''INSERT INTO resources (id,xdmod_resource_id,name,enabled)
                        VALUES(%s,%s,%s,%s);''',
                        (resource_id_in_AKDB,resource_id,resource_name,0))
            db.commit()

            log.info("Resource configuration is in "+file_path)

def validate_resource_id(resource_id,resources):
    try:
        resource_id=int(resource_id)
    except:
        return False
    for _,resource_id2 in resources:
        if int(resource_id2)==int(resource_id):
            return True
    if resource_id==0:
        return True
    return False

def validate_resource_name(resource_name):
    if resource_name.strip()=="":
        log.error("Bad name for resource, try a different name")
        return False
    #check config file presence
    file_path = os.path.abspath(os.path.join(resources_dir, resource_name))
    if os.path.exists(file_path):
        log.error("Resource configuration directory (%s) for resource with name %s already present on file system, try a different name"%(file_path,resource_name,))
        return False
    
    
    #check the entry in mod_appkernel
    dbAK,curAK=akrrcfg.getAKDB(True)
        
    curAK.execute('''SELECT * FROM resource WHERE nickname=%s''', (resource_name,))
    resource_in_AKDB = curAK.fetchall()
    if len(resource_in_AKDB)!=0:
        log.error("Resource with name %s already present in mod_appkernel DB, try a different name"%(resource_name,))
        return False
    
    #check the entry in mod_akrr
    db,cur=akrrcfg.getDB(True)
        
    cur.execute('''SELECT * FROM resources WHERE name=%s''', (resource_name,))
    resource_in_DB = cur.fetchall()
    if len(resource_in_DB)!=0:
        log.error("Resource with name %s already present in mod_akrr DB, try a different name"%(resource_name,))
        return False
    
    return True

def get_resourcename_by_id(resource_id,resources):
    try:
        resource_id=int(resource_id)
    except:
        return False
    for resource_name,resource_id2 in resources:
        if int(resource_id2)==int(resource_id):
            return resource_name
    return None
def validate_queuing_system(queuing_system):
    if queuing_system in ['slurm', 'pbs']:
        return True
    else:
        return False
def checkConnectionToResource():
    successfullyConnected=False
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPassword4thisSession
    global sshPrivateKeyFile
    global sshPrivateKeyPassword
    passphraseEntranceCount=0
    authorizeKeyCount=0
    while True:
        str_io=io.StringIO()
        try:
            sys.stdout = sys.stderr = str_io
            akrrcfg.sshAccess(remoteAccessNode, ssh=remoteAccessMethod, username=sshUserName, password=sshPassword,
                    PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=sshPrivateKeyPassword, logfile=str_io,
                    command='ls')
            
            sys.stdout=sys.__stdout__
            sys.stderr=sys.__stderr__
            
            successfullyConnected=True
            break
        except Exception:
            sys.stdout=sys.__stdout__
            sys.stderr=sys.__stderr__
            if verbose:
                log.debug(
                    "Had attempted to access resource without password and failed, below is resource response"+
                    "="*80+
                    str_io.getvalue()+
                    "="*80)
            #check if it asking for passphrase
            m=re.search(r"Enter passphrase for key '(.*)':",str_io.getvalue())
            if m:
                if passphraseEntranceCount>=3:
                    sshPrivateKeyPassword=None
                    sshPrivateKeyFile=None
                    break
                if passphraseEntranceCount>0:
                    log.error("Incorrect passphrase try again")
                sshPrivateKeyFile=m.group(1)
                log_input("Enter passphrase for key '%s':"%sshPrivateKeyFile)
                sshPrivateKeyPassword=getpass.getpass("")
                passphraseEntranceCount+=1
                continue
            m2=re.search(r"[pP]assword:",str_io.getvalue())
            if m==None and sshPrivateKeyFile!=None and m2:
                log.warning("Can not login to head node. Probably the public key of private key was not authorized on head node")
                print("Will try to add public key to list of authorized keys on head node")
                while True:
                    try:
                        authorizeKeyCount+=1
                        log_input("Enter password for %s@%s (will be used only during this session):"%(sshUserName,remoteAccessNode))
                        sshPassword4thisSession=getpass.getpass("")
                        print()
                        str_io=io.StringIO()
                        sys.stdout = sys.stderr = str_io
                        akrrcfg.sshAccess(remoteAccessNode, ssh='ssh-copy-id', username=sshUserName, password=sshPassword4thisSession,
                            PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=None, logfile=str_io,
                            command='')
                    
                        sys.stdout=sys.__stdout__
                        sys.stderr=sys.__stderr__
                        print(str_io.getvalue())
                        #successfullyConnected=True
                        log.info("Have added public key to list of authorized keys on head node, will attempt to connect again.")
                        print()
                        break
                    except Exception:
                        sys.stdout=sys.__stdout__
                        sys.stderr=sys.__stderr__
                        if verbose:
                            log.debug(
                                "Had attempted to add public key to list of authorized keys on head node and failed, below is resource response"+
                                "="*80+
                                str_io.getvalue()+
                                "="*80)
                        log.error("Incorrect password try again.")
                        if authorizeKeyCount>=3:
                            break
                if authorizeKeyCount<3:
                    continue       
            break
    return successfullyConnected
def getRemoteAccessMethod():
    global resourceName
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPassword4thisSession
    global sshPrivateKeyFile
    global sshPrivateKeyPassword
    global rsh
    global no_ping
    #set remoteAccessNode
    while True:
        log_input("Enter Resource head node (access node) full name (e.g. headnode.somewhere.org):")
        remoteAccessNode=input("[%s] "%resourceName)
        if remoteAccessNode.strip()=="":
            remoteAccessNode=resourceName
        
        response = os.system("ping -c 1 -w2 " + remoteAccessNode + " > /dev/null 2>&1")
        
        if response==0:
            break
        else:
            if no_ping:
                log.warning("Can not ping %s, but asked to ignore it.",remoteAccessNode)
                break
            log.error("Incorrect head node name (can not ping %s), try again",remoteAccessNode)
    #set sshUserName
    curentuser=getpass.getuser()
    askForUserName=True
    successfullyConnected=False
    while True:
        if askForUserName:
            log_input("Enter username for resource access:")
            sshUserName=input("[%s] "%curentuser)
            if sshUserName.strip()=="":
                sshUserName=curentuser
            curentuser=sshUserName
        
        #check passwordless access
        if sshPassword==None:
            log.info("Checking for password-less access")
        else:
            log.info("Checking for resource access")
        successfullyConnected=checkConnectionToResource()
        if successfullyConnected:
            if sshPassword==None:
                log.info("Can access resource without password")
            else:
                log.info("Can access resource")
        
        if successfullyConnected==False:
            log.info("Can not access resource without password")
            actionList=[]
            actionList.append(["TryAgain","The private and public keys was generated manually, right now. Try again."])
            #check private keys
            userHomeDir = os.path.expanduser("~")
            privateKeys = [ os.path.join(userHomeDir,'.ssh',f[:-4]) for f in os.listdir(os.path.join(userHomeDir,'.ssh')) if os.path.isfile(os.path.join(userHomeDir,'.ssh',f)) \
                       and f[-4:]=='.pub' and os.path.isfile(os.path.join(userHomeDir,'.ssh',f[:-4]))]
            if len(privateKeys)>0:
                actionList.append(["UseExistingPrivateKey","Use existing private and public key."])
            
            actionList.append(["GenNewKey","Generate new private and public key."])
            actionList.append(["UsePassword","Use password directly."])
            
            print()
            print("Select authentication method:")
            for i in range(len(actionList)):
                print("%3d  %s"%(i,actionList[i][1]))
            while True:
                log_input("Select option from list above:")
                try:
                    action=input("[2] ")
                    if action.strip()=="":action=2
                    else: action=int(action)
                    
                    if action<0 or action>=len(actionList):
                        raise
                    break
                except Exception:
                    log.error("Incorrect entry, try again.")
            
            #do the action
            print()
            if actionList[action][0]=="TryAgain":
                continue
            if actionList[action][0]=="UsePassword":
                log_input("Enter password for %s@%s:"%(sshUserName,remoteAccessNode))
                sshPassword=getpass.getpass("")
                askForUserName=not askForUserName
                continue
            if actionList[action][0]=="UseExistingPrivateKey":
                print("Available private keys:")
                for i in range(len(privateKeys)):
                    print("%3d  %s"%(i,privateKeys[i]))
                while True:
                    log_input("Select key number from list above:")
                    try:
                        iKey=input("")
                        iKey=int(iKey)
                        
                        if iKey<0 or iKey>=len(privateKeys):
                            raise
                        break
                    except Exception:
                        log.error("Incorrect entry, try again.")
                sshPrivateKeyFile=privateKeys[iKey]
                askForUserName=not askForUserName
                continue
            if actionList[action][0]=="GenNewKey":
                count=0
                while True:
                    log_input("Enter password for %s@%s (will be used only during this session):"%(sshUserName,remoteAccessNode))
                    sshPassword4thisSession=getpass.getpass("")
                    sshPassword=sshPassword4thisSession
                    
                    if checkConnectionToResource():
                        break
                    count+=1
                    if count>=3:
                        break
                sshPassword=None
                #generate keys
                log_input("Enter private key name:")
                sshPrivateKeyFile=input("[id_rsa_%s]"%resourceName)
                if sshPrivateKeyFile.strip()=="":
                    sshPrivateKeyFile="id_rsa_%s"%resourceName
                sshPrivateKeyFile=os.path.join(userHomeDir,'.ssh',sshPrivateKeyFile)
                log_input("Enter passphrase for new key (leave empty for passwordless access):")
                sshPrivateKeyPassword=getpass.getpass("")
                os.system("ssh-keygen -t rsa -N \"%s\" -f %s"%(sshPrivateKeyPassword,sshPrivateKeyFile))
                if sshPrivateKeyPassword.strip()=="":
                    sshPrivateKeyPassword=None
                #copy keys
                akrrcfg.sshAccess(remoteAccessNode, ssh='ssh-copy-id', username=sshUserName, password=sshPassword4thisSession,
                            PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=None, logfile=sys.stdout,
                            command='')
                askForUserName=not askForUserName
                continue
        
        if successfullyConnected:
            break
        else:
            log.error("Incorrect resource access credential")
    if successfullyConnected:
        print()
        log.info("Connecting to "+resourceName)
        
        str_io=io.StringIO()
        sys.stdout = sys.stderr = str_io
        rsh=akrrcfg.sshAccess(remoteAccessNode, ssh=remoteAccessMethod, username=sshUserName, password=sshPassword,
                    PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=sshPrivateKeyPassword, logfile=sys.stdout,
                    command=None)
        sys.stdout=sys.__stdout__
        sys.stderr=sys.__stderr__
        
        log.info("              Done")
    print()
    return successfullyConnected
def getSytemCharacteristics():
    global ppn
    while True:   
        try:                 
            log_input("Enter processors (cores) per node count:")
            ppn=int(input(""))
            break
        except Exception:
            log.error("Incorrect entry, try again.")
    
def getFileSytemAccessPoints():
    global resourceName
    global networkScratch
    global localScratch
    global akrrData
    global appKerDir
    
    homeDir=akrrcfg.sshCommand(rsh,"echo $HOME").strip()
    scratchNetworkDir=akrrcfg.sshCommand(rsh,"echo $SCRATCH").strip()
    
    #localScratch
    localScratchDefault="/tmp"
    while True:                    
        log_input("Enter location of local scratch (visible only to single node):")
        localScratch=input("[%s]"%localScratchDefault)
        if localScratch.strip()=="":
            localScratch=localScratchDefault
        status,msg=resource_deploy.CheckDirSimple(rsh, localScratch)
        if status:
            log.info(msg)
            print()
            break
        else:
            log.warning(msg)
            log.warning('local scratch might be have a different location on head node, so if it is by design it is ok')
            print()
            break
    localScratch=akrrcfg.sshCommand(rsh,"echo %s"%(localScratch,)).strip()
    #networkScratch
    networkScratchDefault=""
    if scratchNetworkDir!="":
        networkScratchDefault=scratchNetworkDir
    networkScratchVisible=False
    while True:                    
        log_input("Enter location of network scratch (visible only to all nodes), used for temporary storage of app kernel input/output:")
        if networkScratchDefault!="":
            networkScratch=input("[%s]"%networkScratchDefault)
            if networkScratch.strip()=="":
                networkScratch=networkScratchDefault
        else:
            networkScratch=input("")
            
        if networkScratch=="":
            log.error("Incorrect value for networkScratch, try again")
            continue
        
        
        status,msg=resource_deploy.CheckDir(rsh, networkScratch,exitOnFail=False,tryToCreate=True)
        if status:
            log.info(msg)
            networkScratchVisible=True
            print()
            break
        else:
            log.warning(msg)
            #log.warning('network scratch might be have a different location on head node, so if it is by design it is ok')
            #print
            break
    networkScratch=akrrcfg.sshCommand(rsh,"echo %s"%(networkScratch,)).strip()
    #appKerDir
    appKerDirDefault=os.path.join(homeDir,"appker",resourceName)   
    while True:                    
        log_input("Enter future location of app kernels input and executable files:")
        appKerDir=input("[%s]"%appKerDirDefault)
        if appKerDir.strip()=="":
            appKerDir=appKerDirDefault
        status,msg=resource_deploy.CheckDir(rsh, appKerDir,exitOnFail=False,tryToCreate=True)
        if status:
            log.info(msg)
            print()
            break
        else:
            log.error(msg)
    appKerDir=akrrcfg.sshCommand(rsh,"echo %s"%(appKerDir,)).strip()
    #akrrData
    akrrDataDefault=os.path.join(homeDir,"akrrdata",resourceName)
    if networkScratchVisible:
        akrrDataDefault=os.path.join(networkScratch,"akrrdata",resourceName)
    while True:                    
        log_input("Enter future locations for app kernels working directories (can or even should be on scratch space):")
        akrrData=input("[%s]"%akrrDataDefault)
        if akrrData.strip()=="":
            akrrData=akrrDataDefault
        status,msg=resource_deploy.CheckDir(rsh, akrrData,exitOnFail=False,tryToCreate=True)
        if status:
            log.info(msg)
            print()
            break
        else:
            log.error(msg) 
    akrrData=akrrcfg.sshCommand(rsh,"echo %s"%(akrrData,)).strip()

def resource_config(config):
    """add resource, config should have following members
        dry_run - Dry Run No files will actually be created
        minimalistic - Minimize questions number, configuration files will be edited manually
        no-ping - do not run ping to test headnode name
        verbose
    """
    global verbose
    global dry_run
    global no_ping
    global minimalistic
    global resourceName
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPrivateKeyFile
    global sshPrivateKeyPassword
    global networkScratch
    global localScratch
    global akrrData
    global appKerDir
    global batchScheduler
    global batchJobHeaderTemplate
    
    log.info("Beginning Initiation of New Resource...")
    verbose=config.verbose
    dry_run=config.dry_run
    no_ping=config.no_ping
    minimalistic=config.minimalistic

    log.info("Retrieving Resources from XDMoD Database...")
    # RETRIEVE: the resources from XDMoD
    resources = retrieve_resources()
    log.info(
    print("Found following resources from XDMoD Database:")
    print()
    print("resource_id  name")
    
    for resource_name,resource_id in resources:
        print("%11d  %-40s"%(resource_id,resource_name))
    print()
    
    if len(resources)>0:
        while True:
            log_input('Enter resource_id for import (enter 0 for no match):')
            resource_id=input()
            if validate_resource_id(resource_id,resources):
                break
            print("Incorrect resource_id try again")
        print()
        resource_id=int(resource_id)
    else:
        resource_id=0
    
    if resource_id<=0:#i.e. no match from XDMoD DB
        resource_id=None
    exit()
    resource_name=""
    while True:
        if resource_id==None:
            log_input('Enter AKRR resource name:')
            resource_name=input()
        else:
            resource_name2=get_resourcename_by_id(resource_id,resources)
            log_input('Enter AKRR resource name, hit enter to use same name as in XDMoD Database [%s]:'%(resource_name2,))
            resource_name=input()
            if resource_name.strip()=="":
                resource_name=resource_name2
        
        if validate_resource_name(resource_name)==True:
            break
    resourceName=resource_name
    print()       
    
    log_input('Enter queuing system on resource (slurm or pbs): ')
    queuing_system=input()
    while not validate_queuing_system(queuing_system):
        log.error("Incorrect queuing_system try again")
        log_input('Enter queuing system on resource (slurm or pbs): ')
        queuing_system=input()
    
    batchScheduler=queuing_system
    print()
    
    if minimalistic==False:
        getRemoteAccessMethod()
        getSytemCharacteristics()
        getFileSytemAccessPoints()
    
    log.debug("Summary of parameters"+
        "remoteAccessNode: {}".format(remoteAccessNode)+
        "remoteAccessMethod: {}".format(remoteAccessMethod)+
        "remoteCopyMethod: {}".format(remoteCopyMethod)+
        "sshUserName: {}".format(sshUserName)+
        "sshPassword: {}".format(sshPassword)+
        "sshPrivateKeyFile: {}".format(sshPrivateKeyFile)+
        "sshPrivateKeyPassword: {}".format(sshPrivateKeyPassword)+
        "networkScratch: {}".format(networkScratch)+
        "localScratch: {}".format(localScratch)+
        "akrrData: {}".format(akrrData)+
        "appKerDir: {}".format(appKerDir)+
        "batchScheduler: {}".format(batchScheduler)+
        "batchJobHeaderTemplate: {}".format(batchJobHeaderTemplate)+"\n")
        
    generate_resource_config(resource_id, resource_name, queuing_system)
    log.info('Initiation of new resource is completed.')
    log.info('    Edit batchJobHeaderTemplate variable in '+resource_cfg_filename)
    log.info('    and move to resource validation and deployment step.')
    log.info('    i.e. execute:')
    log.info('        akrr resource deploy -r '+resource_name)


def cli_resource_config(parent_parser):
    "configurate new resource to AKRR"
    parser = parent_parser.add_parser('config',
        description=cli_resource_config.__doc__)
    
    parser.add_argument(
        '--dry-run', action='store_true', help="Dsdfdry Run No files will actually be created")
    parser.add_argument(
        '--minimalistic', action='store_true', help="Minimize questions number, configuration files will be edited manually")
    parser.add_argument(
        '--no-ping', action='store_true', help="do not run ping to test headnode name")
    
    def handler(args):
        
        return resource_config(args)
    
    parser.set_defaults(func=handler)
    
