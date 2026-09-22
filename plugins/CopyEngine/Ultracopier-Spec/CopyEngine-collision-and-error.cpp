/** \file copyEngine.cpp
\brief Define the copy engine
\author alpha_one_x86 */

#include "CopyEngine.h"
#include "FolderExistsDialog.h"
#include "DiskSpace.h"

//dialog message
/// \note Can be call without queue because all call will be serialized
void CopyEngine::fileAlreadyExistsSlot(INTERNALTYPEPATH source,INTERNALTYPEPATH destination,bool isSame,TransferThreadImpl * thread)
{
    fileAlreadyExists(source,destination,isSame,thread);
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::errorOnFileSlot(INTERNALTYPEPATH fileInfo,std::string errorString,TransferThreadImpl * thread,const ErrorType &errorType)
{
    errorOnFile(fileInfo,errorString,thread,errorType);
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::folderAlreadyExistsSlot(INTERNALTYPEPATH source,INTERNALTYPEPATH destination,bool isSame,ScanFileOrFolder * thread)
{
    folderAlreadyExists(source,destination,isSame,thread);
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::errorOnFolderSlot(INTERNALTYPEPATH fileInfo,std::string errorString,ScanFileOrFolder * thread,ErrorType errorType)
{
    errorOnFolder(fileInfo,errorString,thread,errorType);
}

//mkpath event
void CopyEngine::mkPathErrorOnFolderSlot(INTERNALTYPEPATH folder,std::string error,ErrorType errorType)
{
    mkPathErrorOnFolder(folder,error,errorType);
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::fileAlreadyExists(INTERNALTYPEPATH source,INTERNALTYPEPATH destination,bool isSame,TransferThreadImpl * thread)
{
    if(stopIt)
        return;
    if(thread==NULL)
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"unable to locate the thread");
        return;
    }
    //load the action
    if(isSame)
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"file is same: "+TransferThread::internalStringTostring(source));
        FileExistsAction tempFileExistsAction=alwaysDoThisActionForFileIsSame;
        if(tempFileExistsAction==FileExists_NotSet)
        {
            //no same-file choice yet: a global Skip/Rename still applies, overwriting a file with itself never does
            tempFileExistsAction=alwaysDoThisActionForFileExists;
            if(tempFileExistsAction!=FileExists_Skip && tempFileExistsAction!=FileExists_Rename)
                tempFileExistsAction=FileExists_NotSet;
        }
        switch(tempFileExistsAction)
        {
            case FileExists_Skip:
            case FileExists_Rename:
                thread->setFileExistsAction(tempFileExistsAction);
            break;
            default:
                if(dialogIsOpen)
                {
                    alreadyExistsQueueItem newItem;
                    newItem.source=source;
                    newItem.destination=destination;
                    newItem.isSame=isSame;
                    newItem.transfer=thread;
                    newItem.scan=NULL;
                    alreadyExistsQueue.push_back(newItem);
                    return;
                }
                dialogIsOpen=true;
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");
                FileIsSameDialog *dialog=FileIsSameDialog::createInstance(uiinterface,source,firstRenamingRule,otherRenamingRule,facilityEngine);
                emit isInPause(true);
                dialog->exec();/// \bug crash when external close
                const FileExistsAction newAction=dialog->getAction();
                const bool dialogAlways=dialog->getAlways();
                const std::string dialogNewName=dialog->getNewName();
                delete dialog;// destroyed before any early return below -> no leak on the Cancel path
                emit isInPause(enginePaused);
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
                if(newAction==FileExists_Cancel)
                {
                    emit cancelAll();
                    return;
                }
                if(dialogAlways)
                    alwaysDoThisActionForFileIsSame=newAction;
                if(dialogAlways || newAction!=FileExists_Rename)
                    thread->setFileExistsAction(newAction);
                else
                    thread->setFileRename(dialogNewName);
                dialogIsOpen=false;
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
                //always call to check if pending dialog
                emit queryOneNewDialog();
                return;
            break;
        }
    }
    else
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"file already exists: "+TransferThread::internalStringTostring(source)+
                                 ", destination: "+TransferThread::internalStringTostring(destination));
        FileExistsAction tempFileExistsAction=alwaysDoThisActionForFileExists;
        switch(tempFileExistsAction)
        {
            case FileExists_Skip:
            case FileExists_Rename:
            case FileExists_Overwrite:
            case FileExists_OverwriteIfNewer:
            case FileExists_OverwriteIfOlder:
            case FileExists_OverwriteIfNotSameMdate:
            case FileExists_OverwriteIfNotSameSize:
            case FileExists_OverwriteIfNotSameSizeAndDate:
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"always do this action: "+std::to_string(tempFileExistsAction));
                thread->setFileExistsAction(tempFileExistsAction);
            break;
            default:
                if(dialogIsOpen)
                {
                    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"dialog open, put in queue: "+
                                             TransferThread::internalStringTostring(source)+" "+
                                             TransferThread::internalStringTostring(destination)
                                 );
                    alreadyExistsQueueItem newItem;
                    newItem.source=source;
                    newItem.destination=destination;
                    newItem.isSame=isSame;
                    newItem.transfer=thread;
                    newItem.scan=NULL;
                    alreadyExistsQueue.push_back(newItem);
                    return;
                }
                dialogIsOpen=true;
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");
                FileExistsDialog *dialog=FileExistsDialog::createInstance(uiinterface,source,destination,firstRenamingRule,otherRenamingRule,facilityEngine);
                emit isInPause(true);
                dialog->exec();/// \bug crash when external close
                const FileExistsAction newAction=dialog->getAction();
                const bool dialogAlways=dialog->getAlways();
                const std::string dialogNewName=dialog->getNewName();
                delete dialog;// destroyed before any early return below -> no leak on the Cancel path
                emit isInPause(enginePaused);
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
                if(newAction==FileExists_Cancel)
                {
                    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"cancel");
                    emit cancelAll();
                    return;
                }
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: newAction!=FileExists_Cancel");
                if(dialogAlways && newAction!=alwaysDoThisActionForFileExists)
                {
                    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: always");
                    alwaysDoThisActionForFileExists=newAction;
                    listThread->setAlwaysFileExistsAction(alwaysDoThisActionForFileExists);
                    if(uiIsInstalled)
                        switch(newAction)
                        {
                            default:
                            case FileExists_Skip:
                                ui->comboBoxFileCollision->setCurrentIndex(1);
                            break;
                            case FileExists_Rename:
                                ui->comboBoxFileCollision->setCurrentIndex(6);
                            break;
                            case FileExists_Overwrite:
                                ui->comboBoxFileCollision->setCurrentIndex(2);
                            break;
                            case FileExists_OverwriteIfNotSameMdate:
                                ui->comboBoxFileCollision->setCurrentIndex(8);
                            break;
                            case FileExists_OverwriteIfNewer:
                                ui->comboBoxFileCollision->setCurrentIndex(4);
                            break;
                            case FileExists_OverwriteIfOlder:
                                ui->comboBoxFileCollision->setCurrentIndex(5);
                            break;
                            case FileExists_OverwriteIfNotSameSize:
                                ui->comboBoxFileCollision->setCurrentIndex(7);
                            break;
                            case FileExists_OverwriteIfNotSameSizeAndDate:
                                ui->comboBoxFileCollision->setCurrentIndex(3);
                            break;
                        }
                }
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: post always");
                if(dialogAlways || newAction!=FileExists_Rename)
                    thread->setFileExistsAction(newAction);
                else
                    thread->setFileRename(dialogNewName);
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: post set thread");
                dialogIsOpen=false;
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
                //always call to check if pending dialog
                emit queryOneNewDialog();
                return;
            break;
        }
    }
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"stop");
}

void CopyEngine::haveNeedPutAtBottom(bool needPutAtBottom, const INTERNALTYPEPATH &fileInfo, const std::string &errorString,TransferThreadImpl *thread,const ErrorType &errorType)
{
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"start");
    if(!needPutAtBottom)
    {
        alwaysDoThisActionForFileError=FileError_NotSet;
        if(uiIsInstalled)
            ui->comboBoxFileError->setCurrentIndex(0);
        errorQueueItem newItem;
        newItem.errorString=errorString;
        newItem.inode=fileInfo;
        newItem.mkPath=false;
        newItem.rmPath=false;
        newItem.scan=NULL;
        newItem.transfer=thread;
        newItem.errorType=errorType;
        errorQueue.push_back(newItem);
        showOneNewDialog();
    }
}

void CopyEngine::missingDiskSpace(std::vector<Diskspace> list)
{
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");
    DiskSpace *dialog=DiskSpace::createInstance(facilityEngine,list,uiinterface);
    emit isInPause(true);
    dialog->exec();/// \bug crash when external close
    const bool ok=dialog->getAction();
    delete dialog;
    emit isInPause(enginePaused);
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"cancel: "+std::to_string(ok));
    if(!ok)
        emit cancelAll();
    else
        emit signal_autoStartIfNeeded();// on the list thread: it walks the scheduler's lists
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::errorOnFile(INTERNALTYPEPATH fileInfo, std::string errorString, TransferThreadImpl * thread, const ErrorType &errorType)
{
    if(stopIt)
        return;
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"file have error: "+TransferThread::internalStringTostring(fileInfo)+
                             ", error: "+errorString);
    if(thread==NULL)
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"unable to locate the thread");
        return;
    }
    //load the action
    FileErrorAction tempFileErrorAction=alwaysDoThisActionForFileError;
    // "Put to end" gives each failing file ONE deferred retry: it is sent to the bottom and re-tried once
    // after the rest of the pass. If it has ALREADY been deferred at least once (thread->deferCount>=1) and
    // still fails, do NOT keep deferring (which would 16x-storm a permanently un-creatable symlink -- Windows
    // 1314 -- or a dead sector, and appear hung). Escalate to the Ask dialog so the user decides: Retry (a
    // genuinely flaky sector recovers) or Skip. This runs HERE, while the transfer thread is in its clean
    // error-wait state (transfer_stat=Transfer, NOT in the put-to-end drain), so thread->skip() of an errored
    // symlink completes cleanly on the io_uring/IOCP pipelined backend -- unlike a skip that races an
    // in-flight put-to-end drain (which strands the entry). See the CLAUDE.md "Put to end" note.
    if(tempFileErrorAction==FileError_PutToEndOfTheList && thread!=NULL && thread->deferCount>=1)
        tempFileErrorAction=FileError_NotSet;// -> the interactive Ask dialog (default case below)
    switch(tempFileErrorAction)
    {
        case FileError_Skip:
            // PURE skip, no put-to-end retry -> let the async close handshake finish a write that never
            // opened / was pre-closed, so it frees the cap=1 large slot (skip_drops_multichunk).
            thread->finalSkipNoRetry=true;
            thread->skip();
        return;
        case FileError_Retry:
            thread->retryAfterError();
        return;
        case FileError_PutToEndOfTheList:
            // Defer this file to the end and keep the policy in force for every other file.
            // NEVER reset alwaysDoThisActionForFileError here: doing so dropped headless back to
            // the (blocking, offscreen-invisible) error dialog on the next fault and stalled the
            // whole backup. Giving up on a permanently-dead file is handled per-file, with a bounded
            // retry counter, in ListThread::transferPutAtBottom -- so a transient sector still
            // recovers on retry while a dead one is eventually skipped without aborting the job.
            emit getNeedPutAtBottom(fileInfo,errorString,thread,errorType);
        return;
        case FileError_Cancel:
        return;
        default:
        {
            // Interactive ask: show the error dialog. It is created through FileErrorDialog::createInstance
            // so a headless test build can substitute a subclass that returns a scripted action and never
            // shows the GUI -- there is NO test code, #ifdef or env hook in this engine path.
            if(dialogIsOpen)
            {
                errorQueueItem newItem;
                newItem.errorString=errorString;
                newItem.inode=fileInfo;
                newItem.mkPath=false;
                newItem.rmPath=false;
                newItem.scan=NULL;
                newItem.transfer=thread;
                newItem.errorType=errorType;
                errorQueue.push_back(newItem);
                return;
            }
            dialogIsOpen=true;
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");

            uint64_t size=0;
            uint64_t mdate=0;
            #ifdef Q_OS_WIN32
            WIN32_FILE_ATTRIBUTE_DATA sourceW;
            if(GetFileAttributesExW(fileInfo.c_str(),GetFileExInfoStandard,&sourceW))
            {
                LARGE_INTEGER li;
                li.LowPart  = sourceW.ftLastWriteTime.dwLowDateTime;
                li.HighPart = sourceW.ftLastWriteTime.dwHighDateTime;
                mdate=(li.QuadPart - 0x019DB1DED53E8000) / 10000000;
                /*mdate=sourceW.ftLastWriteTime.dwHighDateTime;
                mdate<<=32;
                mdate|=sourceW.ftLastWriteTime.dwLowDateTime;*/
                size=sourceW.nFileSizeHigh;
                size<<=32;
                size|=sourceW.nFileSizeLow;
            }
            #else
            struct stat source_statbuf;
            #ifdef Q_OS_UNIX
            if(lstat(TransferThread::internalStringTostring(fileInfo).c_str(), &source_statbuf)==0)
            #else
            if(stat(TransferThread::internalStringTostring(fileInfo).c_str(), &source_statbuf)==0)
            #endif
            {
                #ifdef Q_OS_UNIX
                    #ifdef Q_OS_MAC
                    mdate=source_statbuf.st_mtimespec.tv_sec;
                    #else
                    mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtim);
                    #endif
                #else
                mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtime);
                #endif
                size=source_statbuf.st_size;
            }
            #endif

            emit error(TransferThread::internalStringTostring(fileInfo),size,mdate,errorString);
            FileErrorDialog *dialog=FileErrorDialog::createInstance(uiinterface,fileInfo,errorString,errorType,facilityEngine);
            emit isInPause(true);
            dialog->exec();/// \bug crash when external close
            const FileErrorAction newAction=dialog->getAction();
            const bool dialogAlways=dialog->getAlways();
            delete dialog;// destroyed before any early return below -> no leak on the Cancel path
            emit isInPause(enginePaused);
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
            if(newAction==FileError_Cancel)
            {
                emit cancelAll();
                return;
            }
            if(dialogAlways && newAction!=FileError_Retry && newAction!=alwaysDoThisActionForFileError)
            {
                alwaysDoThisActionForFileError=newAction;
                if(uiIsInstalled)
                    switch(newAction)
                    {
                        default:
                        case FileError_Skip:
                            ui->comboBoxFileError->setCurrentIndex(1);
                        break;
                        case FileError_PutToEndOfTheList:
                            ui->comboBoxFileError->setCurrentIndex(2);
                        break;
                    }
            }
            switch(newAction)
            {
                case FileError_Skip:
                    // PURE skip from the dialog -> same as the headless FileError_Skip above: mark it a
                    // final no-retry skip so the async close handshake frees the cap=1 large slot. Without
                    // this the next large file is LOST after a multichunk read-fault skip (file_error_dialog).
                    thread->finalSkipNoRetry=true;
                    thread->skip();
                break;
                case FileError_Retry:
                    thread->retryAfterError();
                break;
                case FileError_PutToEndOfTheList:
                    thread->putAtBottom();
                break;
                default:
                    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Warning,"file error action wrong");
                break;
            }
            dialogIsOpen=false;
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
            //always call to check if pending dialog
            emit queryOneNewDialog();
            return;
        break;
        }
    }
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"stop");
}

/// \note Can be call without queue because all call will be serialized
void CopyEngine::folderAlreadyExists(INTERNALTYPEPATH source, INTERNALTYPEPATH destination, bool isSame, ScanFileOrFolder * thread)
{
    if(stopIt)
        return;
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"folder already exists: "+TransferThread::internalStringTostring(source)+
                             ", destination: "+TransferThread::internalStringTostring(destination));
    if(thread==NULL)
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"unable to locate the thread");
        return;
    }
    //load the always action
    FolderExistsAction tempFolderExistsAction=alwaysDoThisActionForFolderExists;
    switch(tempFolderExistsAction)
    {
        case FolderExists_Skip:
        case FolderExists_Rename:
        case FolderExists_Merge:
            thread->setFolderExistsAction(tempFolderExistsAction);
        break;
        default:
            if(dialogIsOpen)
            {
                alreadyExistsQueueItem newItem;
                newItem.source=source;
                newItem.destination=destination;
                newItem.isSame=isSame;
                newItem.transfer=NULL;
                newItem.scan=thread;
                alreadyExistsQueue.push_back(newItem);
                return;
            }
            dialogIsOpen=true;
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");
            FolderExistsDialog *dialog=FolderExistsDialog::createInstance(uiinterface,source,isSame,destination,firstRenamingRule,otherRenamingRule);
            dialog->exec();/// \bug crash when external close
            const FolderExistsAction newAction=dialog->getAction();
            const bool dialogAlways=dialog->getAlways();
            const std::string dialogNewName=dialog->getNewName();
            delete dialog;// destroyed before any early return below -> no leak on the Cancel path
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
            if(newAction==FolderExists_Cancel)
            {
                emit cancelAll();
                return;
            }
            if(dialogAlways && newAction!=alwaysDoThisActionForFolderExists)
                setComboBoxFolderCollision(newAction);
            if(!dialogAlways && newAction==FolderExists_Rename)
                thread->setFolderExistsAction(newAction,dialogNewName);
            else
                thread->setFolderExistsAction(newAction);
            dialogIsOpen=false;
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
            //always call to check if pending dialog
            emit queryOneNewDialog();
            return;
        break;
    }
}

/// \note Can be call without queue because all call will be serialized
/// \todo all this part
void CopyEngine::errorOnFolder(INTERNALTYPEPATH fileInfo, std::string errorString, ScanFileOrFolder * thread, ErrorType errorType)
{
    if(stopIt)
        return;
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"file have error: "+TransferThread::internalStringTostring(fileInfo)+", error: "+errorString);
    if(thread==NULL)
    {
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"unable to locate the thread");
        return;
    }
    //load the always action
    FileErrorAction tempFileErrorAction=alwaysDoThisActionForFolderError;
    switch(tempFileErrorAction)
    {
        case FileError_Skip:
        case FileError_Retry:
        case FileError_PutToEndOfTheList:
            thread->setFolderErrorAction(tempFileErrorAction);
        break;
        default:
            if(dialogIsOpen)
            {
                errorQueueItem newItem;
                newItem.errorString=errorString;
                newItem.inode=fileInfo;
                newItem.mkPath=false;
                newItem.rmPath=false;
                newItem.scan=thread;
                newItem.transfer=NULL;
                newItem.errorType=errorType;
                errorQueue.push_back(newItem);
                return;
            }
            dialogIsOpen=true;

            uint64_t size=0;
            uint64_t mdate=0;
            #ifdef Q_OS_WIN32
            WIN32_FILE_ATTRIBUTE_DATA sourceW;
            if(GetFileAttributesExW(fileInfo.c_str(),GetFileExInfoStandard,&sourceW))
            {
                LARGE_INTEGER li;
                li.LowPart  = sourceW.ftLastWriteTime.dwLowDateTime;
                li.HighPart = sourceW.ftLastWriteTime.dwHighDateTime;
                mdate=(li.QuadPart - 0x019DB1DED53E8000) / 10000000;
                /*mdate=sourceW.ftLastWriteTime.dwHighDateTime;
                mdate<<=32;
                mdate|=sourceW.ftLastWriteTime.dwLowDateTime;*/
                size=sourceW.nFileSizeHigh;
                size<<=32;
                size|=sourceW.nFileSizeLow;
            }
            #else
            struct stat source_statbuf;
            #ifdef Q_OS_UNIX
            if(lstat(TransferThread::internalStringTostring(fileInfo).c_str(), &source_statbuf)==0)
            #else
            if(stat(TransferThread::internalStringTostring(fileInfo).c_str(), &source_statbuf)==0)
            #endif
            {
                #ifdef Q_OS_UNIX
                    #ifdef Q_OS_MAC
                    mdate=source_statbuf.st_mtimespec.tv_sec;
                    #else
                    mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtim);
                    #endif
                #else
                mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtime);
                #endif
                size=source_statbuf.st_size;
            }
            #endif

            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");
            emit error(TransferThread::internalStringTostring(fileInfo),size,mdate,errorString);
            FileErrorDialog *dialog=FileErrorDialog::createInstance(uiinterface,fileInfo,errorString,errorType,facilityEngine);
            dialog->exec();/// \bug crash when external close
            const FileErrorAction newAction=dialog->getAction();
            const bool dialogAlways=dialog->getAlways();
            delete dialog;// destroyed before any early return below -> no leak on the Cancel path
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
            if(newAction==FileError_Cancel)
            {
                emit cancelAll();
                return;
            }
            // "always" + Retry would retry a permanent error forever with no dialog: Retry stays one-shot
            if(dialogAlways && newAction!=FileError_Retry && newAction!=alwaysDoThisActionForFolderError)
            {
                setComboBoxFolderError(newAction);
                alwaysDoThisActionForFolderError=newAction;
            }
            dialogIsOpen=false;
            thread->setFolderErrorAction(newAction);
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
            //always call to check if pending dialog
            emit queryOneNewDialog();
            return;
        break;
    }
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"stop");
}

// -----------------------------------------------------

//mkpath event
void CopyEngine::mkPathErrorOnFolder(INTERNALTYPEPATH folder, std::string errorString, const ErrorType &errorType)
{
    if(stopIt)
        return;
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"file have error: "+TransferThread::internalStringTostring(folder)+", error: "+errorString);
    //load the always action
    FileErrorAction tempFileErrorAction=alwaysDoThisActionForFolderError;
    switch(tempFileErrorAction)
    {
        case FileError_Skip:
            listThread->mkPathQueue.skip();
        return;
        case FileError_Retry:
            listThread->mkPathQueue.retry();
        return;
        default:
            if(dialogIsOpen)
            {
                errorQueueItem newItem;
                newItem.errorString=errorString;
                newItem.inode=folder;
                newItem.mkPath=true;
                newItem.rmPath=false;
                newItem.scan=NULL;
                newItem.transfer=NULL;
                newItem.errorType=errorType;
                errorQueue.push_back(newItem);
                return;
            }
            dialogIsOpen=true;
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"show dialog");

            uint64_t size=0;
            uint64_t mdate=0;
            #ifdef Q_OS_WIN32
            WIN32_FILE_ATTRIBUTE_DATA sourceW;
            if(GetFileAttributesExW(folder.c_str(),GetFileExInfoStandard,&sourceW))
            {
                LARGE_INTEGER li;
                li.LowPart  = sourceW.ftLastWriteTime.dwLowDateTime;
                li.HighPart = sourceW.ftLastWriteTime.dwHighDateTime;
                mdate=(li.QuadPart - 0x019DB1DED53E8000) / 10000000;
                /*mdate=sourceW.ftLastWriteTime.dwHighDateTime;
                mdate<<=32;
                mdate|=sourceW.ftLastWriteTime.dwLowDateTime;*/
                size=sourceW.nFileSizeHigh;
                size<<=32;
                size|=sourceW.nFileSizeLow;
            }
            #else
            struct stat source_statbuf;
            #ifdef Q_OS_UNIX
            if(lstat(TransferThread::internalStringTostring(folder).c_str(), &source_statbuf)==0)
            #else
            if(stat(TransferThread::internalStringTostring(folder).c_str(), &source_statbuf)==0)
            #endif
            {
                #ifdef Q_OS_UNIX
                    #ifdef Q_OS_MAC
                    mdate=source_statbuf.st_mtimespec.tv_sec;
                    #else
                    mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtim);
                    #endif
                #else
                mdate=*reinterpret_cast<int64_t*>(&source_statbuf.st_mtime);
                #endif
                size=source_statbuf.st_size;
            }
            #endif

            emit error(TransferThread::internalStringTostring(folder),size,mdate,errorString);
            FileErrorDialog *dialog=FileErrorDialog::createInstance(uiinterface,folder,errorString,errorType,facilityEngine);
            dialog->exec();/// \bug crash when external close
            const FileErrorAction newAction=dialog->getAction();
            const bool dialogAlways=dialog->getAlways();
            delete dialog;// destroyed before any early return below -> no leak on the Cancel path
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"close dialog: "+std::to_string(newAction));
            if(newAction==FileError_Cancel)
            {
                emit cancelAll();
                return;
            }
            // "always" + Retry would retry a permanent error forever with no dialog: Retry stays one-shot
            if(dialogAlways && newAction!=FileError_Retry && newAction!=alwaysDoThisActionForFolderError)
            {
                setComboBoxFolderError(newAction);
                alwaysDoThisActionForFolderError=newAction;
            }
            dialogIsOpen=false;
            switch(newAction)
            {
                case FileError_Skip:
                    listThread->mkPathQueue.skip();
                break;
                case FileError_Retry:
                    listThread->mkPathQueue.retry();
                break;
                default:
                    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Warning,"Unknow switch case: "+std::to_string(newAction));
                break;
            }
            ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"emit queryOneNewDialog()");
            //always call to check if pending dialog
            emit queryOneNewDialog();
            return;
        break;
    }
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"stop");
}

//show one new dialog if needed
void CopyEngine::showOneNewDialog()
{
    if(stopIt)
        return;
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"alreadyExistsQueue.size(): "+std::to_string(alreadyExistsQueue.size()));
    ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Notice,"errorQueue.size(): "+std::to_string(errorQueue.size()));
    if(dialogIsOpen)
        ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Warning,"dialogIsOpen should false here");
    {
        const std::vector<alreadyExistsQueueItem> alreadyExistsQueue=this->alreadyExistsQueue;
        this->alreadyExistsQueue.clear();
        for(const alreadyExistsQueueItem &u : alreadyExistsQueue)
        {
            if(u.transfer!=NULL)
            {
                fileAlreadyExists(u.source,
                          u.destination,
                          u.isSame,
                          u.transfer);
            }
            else if(u.scan!=NULL)
                folderAlreadyExists(u.source,
                            u.destination,
                            u.isSame,
                            u.scan);
            else
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"bug, no thread actived");
        }
    }
    {
        const std::vector<errorQueueItem> errorQueue=this->errorQueue;
        this->errorQueue.clear();
        for(const errorQueueItem &u : errorQueue)
        {
            if(u.transfer!=NULL)
                errorOnFile(u.inode,u.errorString,u.transfer,u.errorType);
            else if(u.scan!=NULL)
                errorOnFolder(u.inode,u.errorString,u.scan,u.errorType);
            else if(u.mkPath)
                mkPathErrorOnFolder(u.inode,u.errorString,u.errorType);
            else
                ULTRACOPIER_DEBUGCONSOLE(Ultracopier::DebugLevel_Critical,"bug, no thread actived");
        }
    }
}
